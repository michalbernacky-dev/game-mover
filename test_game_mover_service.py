import pathlib
import unittest


class ServiceHardeningTest(unittest.TestCase):
    def test_ea_bind_template_limits_mount_capability_and_uses_only_marker_id(self):
        unit = pathlib.Path("game-mover-ea-bind@.service").read_text(
            encoding="utf-8",
        )
        self.assertIn(
            "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_DAC_OVERRIDE", unit,
        )
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn(
            "python3 -B /opt/game_mover/game_mover_privileged.py --ea-mount %i",
            unit,
        )
        self.assertIn(
            "python3 -B /opt/game_mover/game_mover_privileged.py --ea-unmount %i",
            unit,
        )
        self.assertNotIn("%f", unit)
        self.assertNotIn("PrivateMounts=", unit)
        specification = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        installer = pathlib.Path("install.sh").read_text(encoding="utf-8")
        self.assertIn("game-mover-ea-bind@.service", specification)
        self.assertIn("game-mover-ea-bind@.service", installer)

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
            "Environment=XDG_RUNTIME_DIR=/var/lib/game-platform/runtime",
            "Environment=GAME_MOVER_PRIVILEGED_HELPER=1",
        )
        for directive in required:
            with self.subTest(directive=directive):
                self.assertIn(directive, unit)

        self.assertNotIn("User=root", unit)
        self.assertNotIn("ReadWritePaths=/run/user/", unit)

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

    def test_deploy_repairs_legacy_rootless_server_data_without_following_symlinks(self):
        specification = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        installer = pathlib.Path("install.sh").read_text(encoding="utf-8")

        self.assertIn("python3 -I -B /opt/game_mover/game_mover_acl.py || exit 1", specification)
        self.assertIn('python3 -I -B "${INSTALL_DIR}/game_mover_acl.py"', installer)
        self.assertNotIn("setfacl -R -P", specification)
        self.assertNotIn("setfacl -R -P", installer)
        self.assertIn("-L /var/lib/game-platform/servers", specification)
        self.assertIn('-L "${PODMAN_HOME}/servers"', installer)

    def test_rpm_provides_the_service_account_through_sysusers(self):
        specification = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        sysusers = pathlib.Path("game-mover.sysusers").read_text(encoding="utf-8")

        self.assertIn("%{_sysusersdir}/game-mover.conf", specification)
        self.assertIn("u gameplatform -", sysusers)
        self.assertIn("g gemers -", sysusers)

    def test_both_install_paths_prepare_the_ea_shared_root(self):
        specification = pathlib.Path("game-mover.spec").read_text(encoding="utf-8")
        installer = pathlib.Path("install.sh").read_text(encoding="utf-8")

        for source in (specification, installer):
            with self.subTest(source=source[:20]):
                self.assertIn("/var/Games/EA", source)
                self.assertIn("setfacl -m", source)


if __name__ == "__main__":
    unittest.main()
