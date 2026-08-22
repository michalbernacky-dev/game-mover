import unittest

from game_mover_security import (
    GLOBAL_OPERATION_DEFAULTS,
    POLICY_MODES,
    SERVER_ACTION_DEFAULTS,
    disabled_security_config,
    global_policy,
    normalize_security_config,
    public_security_payload,
    server_policy,
    validate_security_update,
)


class SecurityPolicyTest(unittest.TestCase):
    def setUp(self):
        self.servers = [
            {
                "id": "forge", "name": "Forge",
                "permissions": {
                    "start": "pam", "stop": "silent",
                    "restart": "disabled", "backup": "pam",
                },
            },
            {"id": "vanilla", "name": "Vanilla", "control_auth": "silent"},
        ]

    def test_missing_config_migrates_legacy_server_policies_and_global_defaults(self):
        config = normalize_security_config(None, self.servers)
        self.assertEqual(config["global"], GLOBAL_OPERATION_DEFAULTS)
        self.assertEqual(config["servers"]["forge"], {
            "start": "pam", "stop": "silent",
            "restart": "disabled", "backup": "pam",
        })
        self.assertEqual(config["servers"]["vanilla"], SERVER_ACTION_DEFAULTS)

    def test_persisted_security_config_overrides_stale_server_permissions(self):
        config = normalize_security_config({
            "global": {"minecraft.install": "silent"},
            "servers": {"forge": {"start": "disabled"}},
        }, self.servers)
        self.assertEqual(global_policy(config, "minecraft.install"), "silent")
        self.assertEqual(server_policy(config, self.servers[0], "start"), "disabled")
        self.assertEqual(server_policy(config, self.servers[0], "stop"), "silent")

    def test_update_rejects_unknown_or_invalid_operations(self):
        base = normalize_security_config(None, self.servers)
        with self.assertRaises(ValueError):
            validate_security_update({
                **base, "global": {**base["global"], "security.manage": "silent"},
            }, self.servers)
        with self.assertRaises(ValueError):
            validate_security_update({
                **base,
                "servers": {**base["servers"], "ghost": SERVER_ACTION_DEFAULTS},
            }, self.servers)
        with self.assertRaises(ValueError):
            validate_security_update({
                **base, "global": {**base["global"], "gate.routes": "everyone"},
            }, self.servers)

    def test_public_catalog_contains_fixed_timekpr_pam_policy(self):
        config = normalize_security_config(None, self.servers)
        payload = public_security_payload(config, self.servers)
        global_catalog = {item["id"]: item for item in payload["catalog"]["global"]}
        self.assertEqual(global_catalog["minecraft.delete"]["default"], "pam")
        self.assertEqual(global_catalog["launcher.update"]["default"], "pam")
        self.assertEqual(global_catalog["game.move"]["default"], "silent")
        self.assertEqual(global_catalog["game.link"]["default"], "silent")
        self.assertEqual(global_catalog["library.permissions"]["default"], "silent")
        self.assertEqual(global_catalog["steam.cache"]["default"], "silent")
        self.assertEqual(global_catalog["knowledge.manage"]["default"], "pam")
        self.assertEqual(global_catalog["dnsmasq.stop"]["default"], "pam")
        fixed = {item["id"]: item for item in payload["catalog"]["fixed"]}
        self.assertEqual(fixed["timekpr.manage"]["policy"], "pam")
        self.assertEqual(fixed["security.manage"]["policy"], "pam")
        self.assertEqual(tuple(payload["modes"]), POLICY_MODES)

    def test_unreadable_registry_can_fail_closed(self):
        config = disabled_security_config(self.servers)
        self.assertTrue(all(
            policy == "disabled" for policy in config["global"].values()
        ))
        self.assertTrue(all(
            policy == "disabled"
            for policies in config["servers"].values()
            for policy in policies.values()
        ))


if __name__ == "__main__":
    unittest.main()
