import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import game_mover_flask as api
import game_mover_privileged as broker
from game_mover_steam_cache import (
    clear_shader_cache_entries,
    inspect_shader_cache,
    inspect_steam_cache,
)


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

    def test_status_lists_local_shader_cache_without_mutation(self):
        self.download.mkdir()
        shader = self.steamapps / "shadercache"
        (shader / "3125250").mkdir(parents=True)
        (shader / "3125250" / "pipeline.bin").write_bytes(b"x" * 4096)
        (self.steamapps / "appmanifest_3125250.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"3125250"\n'
            '\t"name"\t\t"STORROR Parkour PRO"\n}\n',
            encoding="utf-8",
        )
        before = self.snapshot()

        result = inspect_steam_cache(str(self.steamapps), str(self.games))
        shader_result = inspect_shader_cache(str(self.steamapps))

        self.assertNotIn("shader_cache", result)
        self.assertEqual(shader_result["status"], "local")
        self.assertGreater(shader_result["total_size_bytes"], 0)
        self.assertEqual(shader_result["entries"][0]["app_id"], "3125250")
        self.assertEqual(
            shader_result["entries"][0]["name"], "STORROR Parkour PRO"
        )
        self.assertEqual(self.snapshot(), before)

    def test_clear_shader_cache_removes_only_selected_numeric_appids(self):
        shader = self.steamapps / "shadercache"
        selected = shader / "3125250"
        preserved = shader / "730"
        selected.mkdir(parents=True)
        preserved.mkdir()
        (selected / "pipeline.bin").write_bytes(b"x" * 4096)
        (preserved / "pipeline.bin").write_bytes(b"y" * 2048)

        result = clear_shader_cache_entries(str(self.steamapps), ["3125250"])

        self.assertEqual(result["removed_app_ids"], ["3125250"])
        self.assertGreater(result["freed_bytes"], 0)
        self.assertFalse(selected.exists())
        self.assertTrue(preserved.is_dir())

    def test_clear_shader_cache_rejects_traversal_and_symlink(self):
        shader = self.steamapps / "shadercache"
        shader.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (shader / "3125250").symlink_to(outside, target_is_directory=True)

        for app_ids in (["../outside"], ["3125250"]):
            with self.subTest(app_ids=app_ids), self.assertRaises(ValueError):
                clear_shader_cache_entries(str(self.steamapps), app_ids)
        self.assertTrue(outside.is_dir())

    def test_shader_cache_post_requires_policy_and_dispatches_selected_appids(self):
        api.TIMEKPRA_TOKENS["fixture-session"] = ("alice", time.time() + 60)
        pam = {"X-Timekpr-Token": "fixture-session"}
        with (
            patch.object(api, "operation_policy", return_value="pam"),
            patch.object(
                api, "privileged_call",
                return_value={"removed_app_ids": ["3125250"], "freed_bytes": 4096},
            ) as transport,
        ):
            denied = self.client.post(
                "/clear_steam_shader_cache",
                json={"user": "alice", "app_ids": ["3125250"]},
                **self.local,
            )
            allowed = self.client.post(
                "/clear_steam_shader_cache",
                json={"user": "alice", "app_ids": ["3125250"]},
                headers=pam, **self.local,
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        transport.assert_called_once_with(
            "clear-steam-shader-cache",
            {"user": "alice", "app_ids": ["3125250"]}, timeout=180,
        )

    def test_shader_cache_status_uses_read_only_worker_action(self):
        with patch.object(
            api, "privileged_call",
            return_value={"status": "local", "entries": [], "total_size_bytes": 0},
        ) as transport:
            response = self.client.get(
                "/steam_shader_cache_status?user=alice", **self.local
            )
        self.assertEqual(response.status_code, 200)
        transport.assert_called_once_with(
            "steam-shader-cache-status", {"user": "alice"}, timeout=60,
        )

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
            self.assertEqual(
                self.client.get(
                    "/steam_shader_cache_status?user=alice",
                    environ_base={"REMOTE_ADDR": "192.0.2.10"},
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/clear_steam_shader_cache",
                    json={"user": "alice", "app_ids": ["3125250"]},
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

    def test_shader_clear_worker_launch_drops_to_player_identity(self):
        with (
            patch.object(broker.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(
                broker.subprocess,
                "run",
                return_value=Mock(
                    returncode=0,
                    stdout='{"removed_app_ids":["3125250"],"freed_bytes":4096}',
                    stderr="",
                ),
            ) as run,
        ):
            result = broker._broker_dispatch(
                "clear-steam-shader-cache",
                {"user": "alice", "app_ids": ["3125250"]},
            )
            self.assertEqual(result["removed_app_ids"], ["3125250"])
            self.assertEqual(
                run.call_args.args[0][-2:],
                ["--filesystem-worker", "clear-steam-shader-cache"],
            )
            self.assertEqual(run.call_args.kwargs["user"], 1000)
            self.assertEqual(run.call_args.kwargs["group"], 1000)
            self.assertEqual(run.call_args.kwargs["extra_groups"], [1234])
