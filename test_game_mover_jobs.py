import unittest

from game_mover_jobs import OperationAlreadyRunning, OperationRegistry


class OperationRegistryTest(unittest.TestCase):
    def test_operation_reports_phases_and_completion(self):
        registry = OperationRegistry()
        registry.begin(
            "minecraft-create-alex", kind="minecraft-install",
            target_id="alex", message="Připravuji server",
        )
        registry.update(
            "minecraft-create-alex", phase="pulling",
            message="Stahuji image", progress=35,
        )
        self.assertEqual(registry.snapshot("minecraft-create-alex")["progress"], 35)

        registry.finish("minecraft-create-alex", message="Server je připraven")

        result = registry.snapshot("minecraft-create-alex")
        self.assertFalse(result["running"])
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["progress"], 100)

    def test_same_operation_cannot_run_twice(self):
        registry = OperationRegistry()
        registry.begin(
            "router-deploy", kind="proxy-deploy",
            target_id="gate-router", message="Připravuji proxy",
        )

        with self.assertRaises(OperationAlreadyRunning):
            registry.begin(
                "router-deploy", kind="proxy-deploy",
                target_id="gate-router", message="Připravuji proxy",
            )

    def test_progress_is_bounded_and_fail_is_visible(self):
        registry = OperationRegistry()
        registry.begin(
            "restore-test", kind="restore", target_id="test",
            message="Obnovuji",
        )
        registry.update(
            "restore-test", phase="extracting", message="Rozbaluji", progress=500,
        )
        self.assertEqual(registry.snapshot("restore-test")["progress"], 100)
        registry.fail("restore-test", message="Obnova selhala")
        self.assertEqual(registry.snapshot("restore-test")["phase"], "failed")

    def test_snapshots_can_filter_operation_kind(self):
        registry = OperationRegistry()
        registry.begin("install-a", kind="minecraft-install", target_id="a", message="A")
        registry.begin("proxy", kind="proxy-deploy", target_id="gate", message="Gate")
        self.assertEqual(
            [item["id"] for item in registry.snapshots(kind="minecraft-install")],
            ["install-a"],
        )


if __name__ == "__main__":
    unittest.main()
