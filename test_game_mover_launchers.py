import hashlib
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

import game_mover_flask as backend
import game_mover_launchers as launchers


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeResponse:
    def __init__(self, *, payload=None, body=b""):
        self.payload = payload
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.body


def heroic_payload(version="2.22.1", *, digest=""):
    name = f"Heroic-{version}-linux-x86_64.rpm"
    return {
        "tag_name": f"v{version}",
        "published_at": "2026-08-09T11:42:00Z",
        "html_url": f"https://github.com/{launchers.HEROIC_REPOSITORY}/releases/tag/v{version}",
        "assets": [{
            "name": name,
            "browser_download_url": (
                f"https://github.com/{launchers.HEROIC_REPOSITORY}/releases/download/"
                f"v{version}/{name}"
            ),
            "digest": digest,
            "size": 123,
        }],
    }


class LauncherProviderTest(unittest.TestCase):
    def test_semantic_versions_and_prereleases_are_compared(self):
        self.assertTrue(launchers.version_is_newer("2.22.1", "2.22.0"))
        self.assertTrue(launchers.version_is_newer("2.23.0", "2.23.0-beta.1"))
        self.assertFalse(launchers.version_is_newer("2.22.0", "2.22.0"))

    def test_catalog_marks_missing_and_outdated_launchers(self):
        def runner(command, timeout=30):
            if command[:4] == ["rpm", "-q", "--qf", "%{VERSION}"]:
                versions = {"heroic": "2.22.0", "steam": "1.0"}
                version = versions.get(command[-1], "")
                return completed(command, 0 if version else 1, version)
            if command[:5] == ["dnf", "--cacheonly", "--quiet", "repoquery", "--available"]:
                return completed(command, 0, "steam|1.0\n")
            return completed(command, 1)

        with patch.object(launchers.os.path, "isfile", return_value=False):
            result = launchers.launcher_statuses(
                runner=runner,
                request_get=Mock(return_value=FakeResponse(payload=heroic_payload())),
            )
        by_id = {item["id"]: item for item in result}
        self.assertTrue(by_id["heroic"]["installed"])
        self.assertTrue(by_id["heroic"]["update_available"])
        self.assertFalse(by_id["lutris"]["installed"])
        self.assertFalse(by_id["steam"]["update_available"])

    def test_repo_versions_from_multiple_architectures_stay_separate(self):
        runner = Mock(return_value=completed(
            [], 0, "steam|1.0.0.85\nsteam|1.0.0.87\n",
        ))
        self.assertEqual(launchers.repo_version("steam", runner=runner), "1.0.0.87")
        self.assertTrue(any(value.endswith("\\n") for value in runner.call_args.args[0]))

    def test_release_rejects_asset_outside_exact_official_path(self):
        payload = heroic_payload()
        payload["assets"][0]["browser_download_url"] = "https://example.invalid/heroic.rpm"
        with self.assertRaisesRegex(launchers.LauncherError, "Oficiální RPM"):
            launchers.heroic_release(
                request_get=Mock(return_value=FakeResponse(payload=payload)),
            )

    def test_update_validates_digest_identity_and_uses_dnf(self):
        body = b"test heroic rpm"
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        calls = []

        def runner(command, timeout=30):
            calls.append(list(command))
            if command[:4] == ["rpm", "-q", "--qf", "%{VERSION}"]:
                return completed(command, 0, "2.22.0")
            if command[:2] == ["pgrep", "-f"]:
                return completed(command, 1)
            if command[:2] == ["rpm", "-qp"]:
                return completed(command, 0, "heroic\n2.22.1\nx86_64\n")
            if command[:3] == ["dnf", "install", "-y"]:
                self.assertTrue(os.path.isfile(command[-1]))
                return completed(command, 0)
            return completed(command, 1)

        requester = Mock(side_effect=[
            FakeResponse(payload=heroic_payload(digest=digest)),
            FakeResponse(body=body),
        ])
        result = launchers.update_heroic(runner=runner, request_get=requester)
        self.assertTrue(result["changed"])
        install = next(command for command in calls if command[:3] == ["dnf", "install", "-y"])
        self.assertFalse(os.path.exists(install[-1]))

    def test_running_heroic_must_be_closed(self):
        def runner(command, timeout=30):
            if command[0] == "rpm":
                return completed(command, 0, "2.22.0")
            return completed(command, 0, "1234\n")

        with self.assertRaisesRegex(launchers.LauncherError, "ukonči Heroic"):
            launchers.update_heroic(runner=runner, request_get=Mock())

    def test_verified_rpm_can_be_delegated_to_client_installer(self):
        body = b"verified heroic rpm"
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        runner = Mock(side_effect=[
            completed([], 0, "2.22.0"), completed([], 1),
            completed([], 0, "heroic\n2.22.1\nx86_64\n"),
        ])
        installed_paths = []

        def installer(path):
            self.assertTrue(os.path.isfile(path))
            installed_paths.append(path)
            return completed([], 0)

        result = launchers.update_heroic(
            runner=runner,
            request_get=Mock(side_effect=[
                FakeResponse(payload=heroic_payload(digest=digest)),
                FakeResponse(body=body),
            ]),
            installer=installer,
        )
        self.assertTrue(result["changed"])
        self.assertFalse(os.path.exists(installed_paths[0]))


class LauncherApiTest(unittest.TestCase):
    def setUp(self):
        self.client = backend.app.test_client()
        self.local = {"environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def test_status_exposes_catalog_and_policy(self):
        with (
            patch.object(backend, "launcher_statuses", return_value=[{"id": "heroic"}]),
            patch.object(backend, "operation_policy", return_value="pam"),
        ):
            response = self.client.get("/launchers/status", **self.local)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["launchers"], [{"id": "heroic"}])
        self.assertEqual(response.json["update_policy"], "pam")

    def test_update_requires_policy_and_dispatches_allowlisted_provider(self):
        with patch.object(backend, "require_local_operation", return_value=False):
            denied = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(denied.status_code, 403)
        with (
            patch.object(backend, "require_local_operation", return_value=True),
            patch.object(backend, "update_launcher", return_value={
                "changed": True, "version": "2.22.1", "message": "Hotovo",
            }) as update,
        ):
            response = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with("heroic")

    def test_split_service_authorizes_client_packagekit_without_installing(self):
        with (
            patch.object(backend, "PRIVILEGED_HELPER_ENABLED", True),
            patch.object(backend, "require_local_operation", return_value=True),
            patch.object(backend, "update_launcher") as update,
        ):
            response = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["install_via_client"])
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
