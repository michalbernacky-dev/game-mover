import pathlib
import unittest


class ServiceHardeningTest(unittest.TestCase):
    def test_privileged_api_has_safe_baseline_hardening(self):
        unit = pathlib.Path("game_mover.service").read_text(encoding="utf-8")
        required = (
            "UMask=0027",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectClock=true",
            "ProtectHostname=true",
            "ProtectKernelLogs=true",
            "ProtectKernelModules=true",
            "ProtectControlGroups=true",
            "LockPersonality=true",
            "RestrictRealtime=true",
            "RestrictSUIDSGID=true",
            "SystemCallArchitectures=native",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
            "CapabilityBoundingSet=~CAP_SYS_BOOT CAP_SYS_MODULE",
            "CapabilityBoundingSet=~CAP_SYS_RAWIO CAP_SYS_TIME",
        )
        for directive in required:
            with self.subTest(directive=directive):
                self.assertIn(directive, unit)

    def test_required_filesystem_exceptions_are_documented(self):
        unit = pathlib.Path("game_mover.service").read_text(encoding="utf-8")
        self.assertIn("ProtectHome and", unit)
        self.assertIn("ProtectSystem cannot be enabled", unit)


if __name__ == "__main__":
    unittest.main()
