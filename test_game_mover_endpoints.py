import unittest

from game_mover_endpoints import (
    format_endpoint_config,
    format_endpoint_summary,
    normalize_endpoints,
    parse_endpoint_config,
)


class GameEndpointTest(unittest.TestCase):
    def test_compact_configuration_round_trip(self):
        text = "Game/API=tcp:7778; Game/Query=udp:7778; Reliable=tcp:8888"
        endpoints = parse_endpoint_config(text)
        self.assertEqual(endpoints[0], {
            "name": "Game/API", "protocol": "tcp", "port": 7778,
        })
        self.assertEqual(format_endpoint_config(endpoints), text)
        self.assertEqual(
            format_endpoint_summary(endpoints),
            "Game/API: TCP/7778 · Game/Query: UDP/7778 · Reliable: TCP/8888",
        )
        sourced = [{**endpoints[0], "source": "systemd ExecStart"}]
        self.assertEqual(
            format_endpoint_summary(sourced, include_source=True),
            "Game/API: TCP/7778 (systemd ExecStart)",
        )

    def test_rejects_invalid_or_duplicate_endpoint(self):
        with self.assertRaises(ValueError):
            parse_endpoint_config("tcp:7778")
        with self.assertRaises(ValueError):
            normalize_endpoints([
                {"name": "One", "protocol": "tcp", "port": 7778},
                {"name": "Two", "protocol": "tcp", "port": 7778},
            ])


if __name__ == "__main__":
    unittest.main()
