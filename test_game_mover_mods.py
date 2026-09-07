import os
import tempfile
import unittest
import zipfile

from game_mover_mods import compare_inventories, scan_mod_directory


def write_forge_mod(directory, filename, mod_id, version, payload=""):
    path = os.path.join(directory, filename)
    mods_toml = f'''modLoader="javafml"
loaderVersion="[47,)"
license="test"
[[mods]]
modId="{mod_id}"
version="{version}"
displayName="{mod_id}"
'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/mods.toml", mods_toml)
        archive.writestr("payload.txt", payload)


class ModInventoryTest(unittest.TestCase):
    def test_scan_reads_forge_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            write_forge_mod(directory, "carryon.jar", "carryon", "2.1.2.7")
            inventory = scan_mod_directory(directory)
            self.assertEqual(inventory["jar_count"], 1)
            self.assertEqual(inventory["jars"][0]["mods"][0]["id"], "carryon")
            self.assertEqual(inventory["jars"][0]["mods"][0]["version"], "2.1.2.7")

    def test_compare_reports_missing_version_extra_and_content(self):
        with tempfile.TemporaryDirectory() as server_dir, tempfile.TemporaryDirectory() as client_dir:
            write_forge_mod(server_dir, "missing.jar", "missing", "1")
            write_forge_mod(server_dir, "version.jar", "versioned", "2")
            write_forge_mod(server_dir, "changed.jar", "changed", "1", "server")
            write_forge_mod(client_dir, "version.jar", "versioned", "1")
            write_forge_mod(client_dir, "changed.jar", "changed", "1", "client")
            write_forge_mod(client_dir, "extra.jar", "extra", "1")

            result = compare_inventories(
                scan_mod_directory(server_dir), scan_mod_directory(client_dir)
            )
            self.assertEqual([item["id"] for item in result["missing"]], ["missing"])
            self.assertEqual([item["id"] for item in result["extra"]], ["extra"])
            self.assertEqual([item["id"] for item in result["version_mismatch"]], ["versioned"])
            self.assertEqual([item["id"] for item in result["content_mismatch"]], ["changed"])


if __name__ == "__main__":
    unittest.main()
