import json
import os
import tempfile
import unittest
from pathlib import Path

from game_mover_whitelist import (
    MinecraftWhitelistError,
    read_whitelist,
    whitelist_command,
)


class MinecraftWhitelistTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_sorted_safe_whitelist(self):
        (self.data / "whitelist.json").write_text(json.dumps([
            {"uuid": "two", "name": "Zed"},
            {"uuid": "one", "name": "Alex"},
            {"uuid": "bad", "name": "invalid name"},
        ]), encoding="utf-8")
        self.assertEqual(
            [item["name"] for item in read_whitelist(str(self.data))],
            ["Alex", "Zed"],
        )

    def test_builds_only_supported_whitelist_commands(self):
        self.assertEqual(whitelist_command("on"), "whitelist on")
        self.assertEqual(whitelist_command("off"), "whitelist off")
        self.assertEqual(whitelist_command("reload"), "whitelist reload")
        self.assertEqual(whitelist_command("add", "Bernye"), "whitelist add Bernye")
        self.assertEqual(whitelist_command("remove", "Alex"), "whitelist remove Alex")
        for action, player in (("list", None), ("add", "Alex; stop"), ("op", "Alex")):
            with self.assertRaises(MinecraftWhitelistError):
                whitelist_command(action, player)

    def test_rejects_symlinked_whitelist(self):
        target = self.data / "outside.json"
        target.write_text("[]", encoding="utf-8")
        directory = self.data / "server"
        directory.mkdir()
        os.symlink(target, directory / "whitelist.json")
        with self.assertRaises(MinecraftWhitelistError):
            read_whitelist(str(directory))
