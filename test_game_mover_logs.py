import os
import tempfile
import unittest
from pathlib import Path

from game_mover_logs import WorkloadLogError, read_minecraft_latest_log


class MinecraftLogReaderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data = Path(self.temp_dir.name) / "data"
        (self.data / "logs").mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_tail_from_persistent_latest_log(self):
        (self.data / "logs" / "latest.log").write_text(
            "first\nsecond\nthird\n", encoding="utf-8",
        )

        result = read_minecraft_latest_log(str(self.data), 2)

        self.assertEqual(result["output"], "second\nthird")
        self.assertEqual(result["source"], "minecraft-file")
        self.assertFalse(result["truncated"])

    def test_absent_latest_log_allows_runtime_fallback(self):
        self.assertIsNone(read_minecraft_latest_log(str(self.data), 50))

    def test_rejects_symlinked_latest_log(self):
        target = Path(self.temp_dir.name) / "outside.log"
        target.write_text("not allowed", encoding="utf-8")
        os.symlink(target, self.data / "logs" / "latest.log")

        with self.assertRaises(WorkloadLogError):
            read_minecraft_latest_log(str(self.data), 50)
