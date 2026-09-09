import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import game_mover_flask as api
import game_mover_privileged as broker


class SteamCacheSecurityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.steamapps = self.root / "player" / "steamapps"
        self.steamapps.mkdir(parents=True)
        self.games = self.root / "games"
        self.games.mkdir()
        self.download = self.steamapps / "downloading"
        self.client = api.app.test_client()
        self.local = {"environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
        for patcher in (
            patch.object(api, "PRIVILEGED_HELPER_ENABLED", True),
            patch.object(broker, "GAMES_ROOT", str(self.games)),
            patch.object(broker, "_steamapps", return_value=str(self.steamapps)),
            patch.object(
                broker.pwd,
                "getpwnam",
                return_value=Mock(
                    pw_uid=1000,
                    pw_gid=1000,
                    pw_dir="/home/alice",
                ),
            ),
            patch.object(api, "load_local_admin_token", return_value="fixture-token"),
            patch.object(api, "TIMEKPRA_TOKENS", {}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self):
        result = {}
        for path in [self.root, *self.root.rglob("*")]:
            metadata = path.lstat()
            result[str(path.relative_to(self.root))] = (
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                os.readlink(path) if path.is_symlink() else None,
            )
        return result

    def test_production_get_inspects_real_fixtures_without_mutation(self):
        # Keep real API routing and handler behavior; replace only the Unix
        # transport and privilege transition. No live broker or player is used.
        for kind in ("local", "missing", "shared", "custom", "unknown"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory(dir=self.root) as fixture,
            ):
                steamapps = Path(fixture)
                download = steamapps / "downloading"
                if kind == "local":
                    download.mkdir()
                    (download / "payload").write_bytes(b"unfinished download")
                elif kind in ("shared", "custom"):
                    target = (
                        self.games / "steam-cache" / "downloading"
                        if kind == "shared"
                        else self.root / "custom"
                    )
                    download.symlink_to(target)
                elif kind == "unknown":
                    download.write_bytes(b"ordinary file")
                for policy in ("disabled", "pam", "silent"):
                    with (
                        patch.object(api, "operation_policy", return_value=policy),
                        patch.object(broker, "_steamapps", return_value=str(steamapps)),
                        patch.object(
                            api,
                            "privileged_call",
                            side_effect=lambda action, params, **kw: (
                                broker._broker_dispatch(action, params)
                            ),
                        ) as transport,
                        patch.object(
                            broker, "_filesystem_as_user", side_effect=broker.dispatch
                        ) as worker,
                    ):
                        before = self.snapshot()
                        response = self.client.get(
                            "/steam_cache_status?user=alice", **self.local
                        )
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.get_json()["status"], kind)
                        self.assertEqual(
                            response.get_json()["download_path"], str(download)
                        )
                        self.assertEqual(self.snapshot(), before)
                        transport.assert_called_once_with(
                            "steam-cache-status", {"user": "alice"}, timeout=30
                        )
                        worker.assert_called_once_with(
                            "steam-cache-status", {"user": "alice"}
                        )

    def test_missing_library_and_invalid_user_do_not_create_data(self):
        with (
            patch.object(
                api,
                "privileged_call",
                side_effect=lambda action, params, **kw: broker.dispatch(
                    action, params
                ),
            ),
            patch.object(broker, "_steamapps", return_value=None),
        ):
            before = self.snapshot()
            response = self.client.get("/steam_cache_status?user=alice", **self.local)
            invalid = self.client.get(
                "/steam_cache_status?user=../../etc", **self.local
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(self.snapshot(), before)

    def test_post_enforces_policy_before_dispatching_mutation(self):
        api.TIMEKPRA_TOKENS["fixture-session"] = ("alice", time.time() + 60)
        admin = {api.LOCAL_ADMIN_TOKEN_HEADER: "fixture-token"}
        pam = {"X-Timekpr-Token": "fixture-session"}
        for policy, headers, allowed in (
            ("disabled", pam, False),
            ("disabled", admin, False),
            ("pam", {}, False),
            ("pam", admin, False),
            ("pam", pam, True),
            ("silent", {}, False),
            ("silent", admin, True),
            ("silent", pam, True),
        ):
            with (
                self.subTest(policy=policy, headers=headers),
                patch.object(api, "operation_policy", return_value=policy),
                patch.object(
                    api, "privileged_call", return_value={"status": "shared"}
                ) as transport,
            ):
                response = self.client.post(
                    "/set_steam_cache",
                    json={"user": "alice"},
                    headers=headers,
                    **self.local,
                )
                self.assertEqual(response.status_code, 200 if allowed else 403)
                if allowed:
                    transport.assert_called_once_with(
                        "set-steam-cache", {"user": "alice"}, timeout=180
                    )
                else:
                    transport.assert_not_called()

    def test_authorized_post_still_moves_cache_through_user_worker(self):
        self.download.mkdir()
        (self.download / "payload").write_bytes(b"unfinished download")
        with (
            patch.object(api, "operation_policy", return_value="silent"),
            patch.object(
                api,
                "privileged_call",
                side_effect=lambda action, params, **kw: broker._broker_dispatch(
                    action, params
                ),
            ),
            patch.object(
                broker, "_filesystem_as_user", side_effect=broker.dispatch
            ) as worker,
            patch.object(broker, "_set_shared_permissions"),
        ):
            response = self.client.post(
                "/set_steam_cache",
                json={"user": "alice"},
                headers={api.LOCAL_ADMIN_TOKEN_HEADER: "fixture-token"},
                **self.local,
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(self.download.is_symlink())
            self.assertEqual(
                (self.games / "steam-cache" / "downloading" / "payload").read_bytes(),
                b"unfinished download",
            )
            worker.assert_called_once_with("set-steam-cache", {"user": "alice"})

    def test_remote_requests_never_reach_broker(self):
        with patch.object(api, "privileged_call") as transport:
            self.assertEqual(
                self.client.get(
                    "/steam_cache_status?user=alice",
                    environ_base={"REMOTE_ADDR": "192.0.2.10"},
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/set_steam_cache",
                    json={"user": "alice"},
                    environ_base={"REMOTE_ADDR": "192.0.2.10"},
                ).status_code,
                403,
            )
            transport.assert_not_called()

    def test_status_worker_launch_drops_to_player_identity(self):
        with (
            patch.object(broker.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(
                broker.subprocess,
                "run",
                return_value=Mock(
                    returncode=0,
                    stdout='{"status":"local"}',
                    stderr="",
                ),
            ) as run,
        ):
            result = broker._broker_dispatch("steam-cache-status", {"user": "alice"})
            self.assertEqual(result, {"status": "local"})
            self.assertEqual(
                run.call_args.args[0][-2:],
                ["--filesystem-worker", "steam-cache-status"],
            )
            self.assertEqual(run.call_args.kwargs["user"], 1000)
            self.assertEqual(run.call_args.kwargs["group"], 1000)
            self.assertEqual(run.call_args.kwargs["extra_groups"], [1234])
