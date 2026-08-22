import tempfile
import unittest
from pathlib import Path

from game_mover_notes import (
    NotesError, delete_target_notes, initialize_notes_database, list_all_notes,
    list_notes, replace_notes,
)


class NotesStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "notes.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_replaces_and_orders_notes_for_multiple_target_types(self):
        replace_notes(self.path, "game", "gta-v-enhanced", [
            {"title": "Wrapper", "platform": "Fedora · Heroic", "body": "Použij fix.bat.",
             "checks": [{"type": "path_exists", "category": "installation",
                         "label": "GTA V", "paths": ["/games/GTAV"]}]},
            {"title": "Proton", "platform": "Linux", "body": "Použij GE-Proton10-15."},
        ])
        replace_notes(self.path, "server", "prominence-2", [
            {"title": "Wayland", "platform": "Fedora", "body": "Použij X11."},
        ])

        game_notes = list_notes(self.path, "game", "gta-v-enhanced")
        self.assertEqual([note["title"] for note in game_notes], ["Wrapper", "Proton"])
        self.assertEqual(game_notes[0]["platform"], "Fedora · Heroic")
        self.assertEqual(game_notes[0]["checks"][0]["type"], "path_exists")
        self.assertEqual(len(list_notes(self.path, "server", "prominence-2")), 1)
        self.assertEqual(
            {(note["target_type"], note["target_id"]) for note in list_all_notes(self.path)},
            {("game", "gta-v-enhanced"), ("server", "prominence-2")},
        )

    def test_rejects_invalid_targets_and_incomplete_notes(self):
        with self.assertRaises(NotesError):
            replace_notes(self.path, "unknown", "target", [])
        with self.assertRaises(NotesError):
            replace_notes(self.path, "game", "valid", [{"title": "", "body": "x"}])
        with self.assertRaises(NotesError):
            replace_notes(self.path, "game", "valid", [{
                "title": "x", "platform": "Linux", "body": "x",
                "checks": [{"type": "shell", "label": "Nebezpečné"}],
            }])
        with self.assertRaises(NotesError):
            replace_notes(self.path, "game", "valid", [{
                "title": "x", "platform": "Linux", "body": "x",
                "checks": [{"type": "glob_absent", "label": "Pomalý glob",
                            "pattern": "~/**/titles.dat"}],
            }])

    def test_deletes_only_selected_target(self):
        note = [{"title": "Tip", "platform": "Linux", "body": "Postup"}]
        replace_notes(self.path, "game", "one", note)
        replace_notes(self.path, "game", "two", note)
        delete_target_notes(self.path, "game", "one")

        self.assertEqual(list_notes(self.path, "game", "one"), [])
        self.assertEqual(len(list_notes(self.path, "game", "two")), 1)

    def test_first_host_initialization_creates_directory_schema_and_empty_database(self):
        fresh_path = str(Path(self.temp_dir.name) / "new-host" / "notes.sqlite3")
        self.assertFalse(Path(fresh_path).exists())

        initialize_notes_database(fresh_path)

        self.assertTrue(Path(fresh_path).is_file())
        self.assertEqual(list_all_notes(fresh_path), [])


if __name__ == "__main__":
    unittest.main()
