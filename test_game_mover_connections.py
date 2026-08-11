import unittest

from game_mover_connections import (
    DEFAULT_SSH_TUNNEL_PORT,
    LOCAL_API_URL,
    is_host_management_mode,
    management_api_url,
    normalize_app_mode,
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


if __name__ == "__main__":
    unittest.main()
