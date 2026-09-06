import pathlib
import unittest


class ServiceHardeningTest(unittest.TestCase):
    def test_network_api_is_unprivileged_and_strictly_sandboxed(self):
        unit = pathlib.Path("game_mover.service").read_text(encoding="utf-8")
        required = (
            "User=gameplatform",
            "Group=gameplatform",
            "SupplementaryGroups=gemers",
            "UMask=0027",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
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
            "CapabilityBoundingSet=\n",
            "AmbientCapabilities=\n",
            "ReadWritePaths=/var/lib/game-mover /var/lib/game-platform",
            "Environment=GAME_MOVER_PRIVILEGED_HELPER=1",
        )
        for directive in required:
            with self.subTest(directive=directive):
                self.assertIn(directive, unit)

        self.assertNotIn("User=root", unit)

    def test_root_broker_has_no_network_and_a_bounded_capability_set(self):
        unit = pathlib.Path("game-mover-privileged.service").read_text(encoding="utf-8")
        self.assertIn("User=root", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertIn("PrivateDevices=true", unit)
        self.assertIn("ProtectProc=invisible", unit)
        self.assertIn("CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE", unit)
        self.assertIn("CAP_SETGID CAP_SETUID", unit)
        self.assertIn("AmbientCapabilities=CAP_SETUID", unit)
        self.assertNotIn("CAP_SYS_ADMIN", unit)
        self.assertNotIn("RestrictSUIDSGID=true", unit)

    def test_acl_replaces_runtime_sgid_creation(self):
        spec = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        backend = pathlib.Path("game_mover_flask.py").read_text(encoding="utf-8")
        self.assertIn("Requires:       acl", spec)
        self.assertIn("setfacl -m", spec)
        self.assertNotIn("chmod 2775", spec)
        self.assertIn('SETFACL_PATH = "/usr/bin/setfacl"', backend)
        self.assertNotIn("os.fchmod(directory_fd, 0o2775)", backend)

    def test_rpm_provides_the_service_account_through_sysusers(self):
        specification = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        sysusers = pathlib.Path("game-mover.sysusers").read_text(encoding="utf-8")

        self.assertIn("%{_sysusersdir}/game-mover.conf", specification)
        self.assertIn("u gameplatform -", sysusers)
        self.assertIn("g gemers -", sysusers)


if __name__ == "__main__":
    unittest.main()
