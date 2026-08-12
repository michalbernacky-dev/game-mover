import unittest

from game_mover_connections import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_TUNNEL_PORT,
    LOCAL_API_URL,
    connection_profile_host,
    is_host_management_mode,
    managed_ssh_tunnel_arguments,
    management_api_url,
    normalize_app_mode,
    normalize_ssh_port,
    normalize_ssh_tunnel_port,
    ssh_tunnel_api_url,
)


class ConnectionModeTest(unittest.TestCase):
    def test_unknown_mode_falls_back_to_read_only_client(self):
        self.assertEqual(normalize_app_mode("unknown"), "client")
        self.assertFalse(is_host_management_mode("unknown"))

    def test_local_server_uses_only_local_backend(self):
        self.assertTrue(is_host_management_mode("server"))
        self.assertEqual(management_api_url("server"), LOCAL_API_URL)

    def test_ssh_management_uses_only_loopback_tunnel_endpoint(self):
        self.assertTrue(is_host_management_mode("ssh_tunnel"))
        self.assertEqual(ssh_tunnel_api_url(5500), "http://127.0.0.1:5500")
        self.assertEqual(
            management_api_url("ssh_tunnel", 5512), "http://127.0.0.1:5512",
        )

    def test_invalid_tunnel_port_falls_back_to_safe_default(self):
        for value in (None, "invalid", 0, 65536):
            with self.subTest(value=value):
                self.assertEqual(
                    normalize_ssh_tunnel_port(value), DEFAULT_SSH_TUNNEL_PORT,
                )

    def test_client_mode_has_no_management_endpoint(self):
        with self.assertRaises(ValueError):
            management_api_url("client")

    def test_connection_profile_host_accepts_lan_tailscale_dns_and_ipv6(self):
        self.assertEqual(connection_profile_host("100.64.0.10:5000"), "100.64.0.10")
        self.assertEqual(connection_profile_host("http://192.0.2.66:5000"), "192.0.2.66")
        self.assertEqual(connection_profile_host("game-host.example:5000"), "game-host.example")
        self.assertEqual(connection_profile_host("[fd7a:115c:a1e0::1]:5000"), "fd7a:115c:a1e0::1")

    def test_connection_profile_host_rejects_ssh_injection(self):
        for address in ("host -oProxyCommand=bad", "user@host:5000", "host/path"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                connection_profile_host(address)

    def test_managed_tunnel_is_key_only_loopback_and_no_agent_forwarding(self):
        arguments = managed_ssh_tunnel_arguments(
            "100.64.0.10", "alice", 5500, 22,
        )
        self.assertIn("BatchMode=yes", arguments)
        self.assertIn("PasswordAuthentication=no", arguments)
        self.assertIn("KbdInteractiveAuthentication=no", arguments)
        self.assertIn("StrictHostKeyChecking=yes", arguments)
        self.assertIn("ExitOnForwardFailure=yes", arguments)
        self.assertIn("ForwardAgent=no", arguments)
        self.assertIn("PermitLocalCommand=no", arguments)
        self.assertEqual(arguments[:2], ["-F", "/dev/null"])
        self.assertIn("127.0.0.1:5500:127.0.0.1:5000", arguments)
        self.assertEqual(arguments[-1], "alice@100.64.0.10")

    def test_managed_tunnel_rejects_bad_user_and_normalizes_ports(self):
        with self.assertRaises(ValueError):
            managed_ssh_tunnel_arguments("server.example", "user;command", 5500)
        self.assertEqual(normalize_ssh_port("bad"), DEFAULT_SSH_PORT)


if __name__ == "__main__":
    unittest.main()
