import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from game_mover_worlds import (
    MinecraftWorldError,
    create_world_archive,
    discover_minecraft_worlds,
    extract_world_archive,
)


class MinecraftWorldTransferTest(unittest.TestCase):
    def test_discovers_vanilla_and_curseforge_worlds(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            vanilla = home / ".minecraft" / "saves" / "Solo"
            curseforge = (
                home / "Documents" / "curseforge" / "minecraft" / "Instances"
                / "Family Pack" / "saves" / "Friends"
            )
            vanilla.mkdir(parents=True)
            curseforge.mkdir(parents=True)
            (vanilla / "level.dat").write_bytes(b"vanilla")
            (curseforge / "level.dat").write_bytes(b"curseforge")

            worlds = discover_minecraft_worlds(temporary)

        self.assertEqual({item["name"] for item in worlds}, {"Solo", "Friends"})
        curseforge_world = next(item for item in worlds if item["name"] == "Friends")
        self.assertEqual(curseforge_world["launcher"], "CurseForge")
        self.assertEqual(curseforge_world["instance"], "Family Pack")

    def test_archive_round_trip_preserves_regular_world_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "region").mkdir(parents=True)
            (source / "level.dat").write_bytes(b"level")
            (source / "region" / "r.0.0.mca").write_bytes(b"region")
            archive = root / "world.tar.gz"

            created = create_world_archive(str(source), str(archive))
            extracted = extract_world_archive(str(archive), str(root / "staging"))

            destination = Path(extracted["world_directory"])
            self.assertEqual((destination / "level.dat").read_bytes(), b"level")
            self.assertEqual((destination / "region" / "r.0.0.mca").read_bytes(), b"region")
            self.assertEqual(created["files"], 2)

    def test_client_archive_rejects_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "level.dat").write_bytes(b"level")
            os.symlink("level.dat", source / "linked.dat")

            with self.assertRaisesRegex(MinecraftWorldError, "symbolické"):
                create_world_archive(str(source), str(root / "world.tar.gz"))

    def test_client_archive_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "level.dat").write_bytes(b"level")
            os.link(source / "level.dat", source / "duplicate.dat")

            with self.assertRaisesRegex(MinecraftWorldError, "vícenásobně"):
                create_world_archive(str(source), str(root / "world.tar.gz"))

    def test_server_extraction_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "world.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                level = tarfile.TarInfo("world/level.dat")
                level.size = 5
                archive.addfile(level, io.BytesIO(b"level"))
                escaped = tarfile.TarInfo("world/../../escaped")
                escaped.size = 3
                archive.addfile(escaped, io.BytesIO(b"bad"))

            with self.assertRaisesRegex(MinecraftWorldError, "neplatnou cestu"):
                extract_world_archive(str(archive_path), str(root / "staging"))
            self.assertFalse((root / "escaped").exists())


if __name__ == "__main__":
    unittest.main()
