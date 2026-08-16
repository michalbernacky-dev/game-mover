import ipaddress
import struct
import tempfile
import unittest
from pathlib import Path

from game_mover_dns import (
    DnsConfigError,
    build_dns_response,
    normalize_dns_config,
    records_from_gate,
    write_runtime_config,
)


def dns_query(name, query_type=1):
    encoded = b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split("."))
    return struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + encoded + b"\0" + struct.pack("!HH", query_type, 1)


class ManagedDnsTest(unittest.TestCase):
    def setUp(self):
        self.config = normalize_dns_config({
            "provider": "builtin",
            "zone": "mc.home.arpa",
            "ttl": 60,
            "listen_addresses": ["192.0.2.66", "100.64.0.10"],
            "answer_addresses": ["192.0.2.66"],
        })
        self.gate = {"routes": [
            {"host": "forge.mc.home.arpa", "backend": {"host": "forge", "port": 25565}},
            {"host": "*", "backend": {"host": "forge", "port": 25565}},
            {"host": "outside.example", "backend": {"host": "test", "port": 25565}},
        ]}

    def test_registry_is_derived_only_from_exact_in_zone_gate_routes(self):
        self.assertEqual(records_from_gate(self.config, self.gate), [{
            "name": "forge.mc.home.arpa", "address": "192.0.2.66",
        }])

    def test_builtin_requires_explicit_listen_and_answer_addresses(self):
        with self.assertRaises(DnsConfigError):
            normalize_dns_config({"provider": "builtin", "answer_addresses": ["192.0.2.1"]})
        with self.assertRaises(DnsConfigError):
            normalize_dns_config({"provider": "builtin", "listen_addresses": ["127.0.0.1"]})
        pihole = normalize_dns_config({
            "provider": "pihole_local", "answer_addresses": ["192.0.2.66"],
        })
        self.assertEqual(pihole["listen_addresses"], [])

    def test_authoritative_response_returns_a_and_nxdomain_without_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = write_runtime_config(str(Path(directory) / "dns.json"), self.config, self.gate)
        response = build_dns_response(dns_query("forge.mc.home.arpa"), runtime)
        _, flags, questions, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
        self.assertEqual((questions, answers), (1, 1))
        self.assertTrue(flags & 0x0400)
        self.assertFalse(flags & 0x0080)
        self.assertTrue(response.endswith(ipaddress.ip_address("192.0.2.66").packed))

        missing = build_dns_response(dns_query("missing.mc.home.arpa"), runtime)
        _, flags, _, answers, _, _ = struct.unpack("!HHHHHH", missing[:12])
        self.assertEqual(flags & 0x000F, 3)
        self.assertEqual(answers, 0)

        outside = build_dns_response(dns_query("example.org"), runtime)
        _, flags, _, answers, _, _ = struct.unpack("!HHHHHH", outside[:12])
        self.assertEqual(flags & 0x000F, 5)
        self.assertFalse(flags & 0x0400)
        self.assertEqual(answers, 0)


if __name__ == "__main__":
    unittest.main()
