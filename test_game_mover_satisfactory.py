import subprocess
import unittest
from unittest.mock import patch

import game_mover_satisfactory as satisfactory


class SatisfactoryAdapterTest(unittest.TestCase):
    def test_reads_explicit_game_and_external_reliable_ports(self):
        ports = satisfactory.parse_satisfactory_exec_start(
            "{ path=/opt/satisfactory/FactoryServer.sh ; "
            "argv[]=/opt/satisfactory/FactoryServer.sh -Port=7778 "
            "-ReliablePort=8889 -ExternalReliablePort=8890 ; }"
        )
        self.assertEqual(ports["game_port"], 7778)
        self.assertEqual(ports["reliable_port"], 8890)
        self.assertEqual(ports["game_source"], "systemd ExecStart")
        self.assertEqual(ports["reliable_source"], "systemd ExecStart")

    def test_uses_documented_defaults_and_decodes_systemd_escapes(self):
        defaults = satisfactory.parse_satisfactory_exec_start("")
        self.assertEqual(defaults["game_port"], 7777)
        self.assertEqual(defaults["reliable_port"], 8888)
        escaped = satisfactory.parse_satisfactory_exec_start(
            "argv[]=FactoryServer.sh\\x20-Port\\x3d7778"
        )
        self.assertEqual(escaped["game_port"], 7778)

    def test_discovery_uses_fixed_systemctl_arguments_without_shell(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout="argv[]=FactoryServer.sh -Port=7778", stderr="",
        )
        with patch.object(satisfactory.subprocess, "run", return_value=completed) as run:
            endpoints = satisfactory.discover_satisfactory_endpoints(
                "satisfactory.service"
            )
        run.assert_called_once_with(
            [
                "systemctl", "show", "--property=ExecStart", "--value",
                "--no-pager", "satisfactory.service",
            ],
            capture_output=True, text=True, timeout=3, check=False,
        )
        self.assertEqual(
            [(item["protocol"], item["port"]) for item in endpoints],
            [("tcp", 7778), ("udp", 7778), ("tcp", 8888)],
        )

    def test_rejects_unvalidated_unit(self):
        with self.assertRaises(ValueError):
            satisfactory.discover_satisfactory_endpoints("x; reboot.service")


if __name__ == "__main__":
    unittest.main()
