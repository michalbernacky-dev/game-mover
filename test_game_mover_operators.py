import json
import os
import tempfile
import unittest
from pathlib import Path

from game_mover_operators import (
    MinecraftOperatorsError,
    operator_command,
    read_operators,
)


class MinecraftOperatorsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_sorted_safe_operator_catalog(self):
        (self.data / "ops.json").write_text(json.dumps([
            {"uuid": "two", "name": "Zed", "level": 3, "bypassesPlayerLimit": True},
            {"uuid": "one", "name": "Alex", "level": 4, "bypassesPlayerLimit": False},
            {"uuid": "bad", "name": "invalid name", "level": 4},
        ]), encoding="utf-8")

        result = read_operators(str(self.data))

        self.assertEqual([item["name"] for item in result], ["Alex", "Zed"])
        self.assertEqual(result[1]["level"], 3)
        self.assertTrue(result[1]["bypasses_player_limit"])

    def test_builds_only_op_and_deop_for_valid_player_names(self):
        self.assertEqual(operator_command("op", "Bernye"), "op Bernye")
        self.assertEqual(operator_command("deop", "Player_2"), "deop Player_2")
        for action, player in (("say", "Alex"), ("op", "Alex; stop"), ("op", "x")):
            with self.assertRaises(MinecraftOperatorsError):
                operator_command(action, player)

    def test_rejects_symlinked_ops_file(self):
        target = self.data / "outside.json"
        target.write_text("[]", encoding="utf-8")
        directory = self.data / "server"
        directory.mkdir()
        os.symlink(target, directory / "ops.json")
        with self.assertRaises(MinecraftOperatorsError):
            read_operators(str(directory))
