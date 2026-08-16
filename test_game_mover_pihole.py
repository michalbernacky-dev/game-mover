import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from game_mover_pihole import PiholeAdapterError, sync_pihole_records


class FakePihole:
    def __init__(self, records):
        self.records = list(records)
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        if len(command) == 3:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.records), stderr="")
        self.records = json.loads(command[3])
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class PiholeAdapterTest(unittest.TestCase):
    def test_preserves_manual_records_and_removes_only_owned_records(self):
        fake = FakePihole([
            "192.0.2.20 printer.example",
            "192.0.2.66 forge.mc.example",
        ])
        with tempfile.TemporaryDirectory() as directory:
            state_path = str(Path(directory) / "state.json")
            result = sync_pihole_records([
                {"name": "forge.mc.example", "address": "192.0.2.66"},
                {"name": "test.mc.example", "address": "192.0.2.66"},
            ], state_path, enabled=True, runner=fake)
            self.assertTrue(result["changed"])
            self.assertEqual(result["managed_records"], 1)
            self.assertEqual(result["reused_records"], 1)
            self.assertEqual(fake.records, [
                "192.0.2.20 printer.example",
                "192.0.2.66 forge.mc.example",
                "192.0.2.66 test.mc.example",
            ])

            sync_pihole_records([], state_path, enabled=False, runner=fake)
            self.assertEqual(fake.records, [
                "192.0.2.20 printer.example",
                "192.0.2.66 forge.mc.example",
            ])

    def test_refuses_to_overwrite_conflicting_manual_record(self):
        fake = FakePihole(["192.0.2.10 forge.mc.example"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PiholeAdapterError, "ruční záznam"):
                sync_pihole_records([
                    {"name": "forge.mc.example", "address": "192.0.2.66"},
                ], str(Path(directory) / "state.json"), enabled=True, runner=fake)
        self.assertEqual(fake.records, ["192.0.2.10 forge.mc.example"])


if __name__ == "__main__":
    unittest.main()
