import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "rpm-build.yml"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"
RUFF_CONFIG_PATH = ROOT / "ruff.toml"
INSTALLER_PATH = ROOT / "install.sh"
RPM_SPEC_PATH = ROOT / "game-mover.spec"
LICENSE_PATH = ROOT / "LICENSE"
NOTICE_PATH = ROOT / "NOTICE"
SECURITY_PATH = ROOT / "SECURITY.md"
TRADEMARKS_PATH = ROOT / "TRADEMARKS.md"


class CiSecurityTest(unittest.TestCase):
    def test_workflow_dependencies_are_immutable_and_token_is_read_only(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertNotIn("fedora:latest", workflow)
        self.assertIn("image: fedora:43", workflow)
        self.assertRegex(workflow, r"(?m)^permissions:\n  contents: read$")
        action_refs = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", workflow)
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_workflow_scans_and_tests_before_building(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        secret_scan = workflow.index("gitleaks detect")
        static_scan = workflow.index("ruff check game_mover*.py")
        tests = workflow.index("python3 -m unittest discover -v")
        build = workflow.index("rpmbuild --define")
        self.assertIn("            acl \\\n", workflow)
        self.assertLess(secret_scan, build)
        self.assertLess(static_scan, build)
        self.assertLess(tests, build)

        ruff_config = RUFF_CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn('select = ["S"]', ruff_config)

    def test_dependabot_monitors_actions_and_python_dependencies(self):
        config = DEPENDABOT_PATH.read_text(encoding="utf-8")

        self.assertIn('package-ecosystem: "github-actions"', config)
        self.assertIn('package-ecosystem: "pip"', config)
        self.assertEqual(config.count('interval: "weekly"'), 2)

    def test_installers_derive_python_payload_from_source_glob(self):
        modules = sorted(path.name for path in ROOT.glob("game_mover*.py"))
        self.assertGreater(len(modules), 1)

        installer = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertEqual(installer.count("--include='/game_mover*.py'"), 1)
        self.assertFalse(any(
            f"--include='/{module}'" in installer for module in modules
        ))

        specification = RPM_SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("for module in game_mover*.py; do", specification)
        self.assertIn("/opt/game_mover/game_mover*.py", specification)
        self.assertFalse(any(
            re.search(rf"(?m)^install .*\b{re.escape(module)}\b", specification)
            for module in modules
        ))

    def test_publication_policy_is_complete_and_packaged(self):
        license_text = LICENSE_PATH.read_text(encoding="utf-8")
        self.assertIn("# PolyForm Noncommercial License 1.0.0", license_text)
        self.assertIn("## Noncommercial Purposes", license_text)
        self.assertIn("## Changes and New Works License", license_text)
        self.assertRegex(
            NOTICE_PATH.read_text(encoding="utf-8"),
            r"^Required Notice: Copyright 2026 \S+\n$",
        )

        security = SECURITY_PATH.read_text(encoding="utf-8")
        self.assertIn("security/advisories/new", security)
        self.assertIn("current release", security)
        self.assertIn("bring-your-own-key", security)
        self.assertIn("unofficial fork", TRADEMARKS_PATH.read_text(encoding="utf-8"))

        specification = RPM_SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("License:        PolyForm-Noncommercial-1.0.0", specification)
        self.assertIn("%license LICENSE NOTICE", specification)

        installer = INSTALLER_PATH.read_text(encoding="utf-8")
        for filename in ("LICENSE", "NOTICE", "SECURITY.md", "TRADEMARKS.md"):
            self.assertIn(f"--include='/{filename}'", installer)

        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.key", gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()
