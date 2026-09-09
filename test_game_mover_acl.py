import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import game_mover_acl as migration


class AclRightsTest(unittest.TestCase):
    def test_widening_mask_preserves_all_other_effective_entries(self):
        acl = {
            (migration.USER_OBJ, migration.UNDEFINED): 6,
            (migration.USER, 1001): 7,
            (migration.USER, 1002): 4,
            (migration.GROUP_OBJ, migration.UNDEFINED): 7,
            (migration.GROUP, 2001): 6,
            (migration.MASK, migration.UNDEFINED): 4,
            (migration.OTHER, migration.UNDEFINED): 0,
        }
        result = migration.grant_access(acl, 1002, 6, owner_uid=3000)
        self.assertEqual(result[migration.USER, 1002], 6)
        self.assertEqual(result[migration.MASK, migration.UNDEFINED], 6)
        for key in (
            (migration.USER, 1001),
            (migration.GROUP_OBJ, migration.UNDEFINED),
            (migration.GROUP, 2001),
        ):
            self.assertEqual(result[key], acl[key] & 4)
        self.assertEqual(result[migration.USER_OBJ, migration.UNDEFINED], 6)
        self.assertEqual(result[migration.OTHER, migration.UNDEFINED], 0)
        self.assertEqual(acl[migration.USER, 1001], 7)

    def test_owner_access_and_future_owner_are_handled_separately(self):
        acl = migration._mode_acl(0o400)
        access = migration.grant_access(acl, 1001, 7, owner_uid=1001)
        default = migration.grant_access(acl, 1001, 7)
        self.assertEqual(access[migration.USER_OBJ, migration.UNDEFINED], 7)
        self.assertEqual(default[migration.USER_OBJ, migration.UNDEFINED], 4)
        self.assertEqual(default[migration.USER, 1001], 7)


class AclFilesystemTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "servers"
        self.root.mkdir(mode=0o700)
        self.uid = os.getuid()
        # Exercise a distinct named principal when outside a single-UID sandbox.
        for line in Path("/proc/self/uid_map").read_text().splitlines():
            start, _outside, count = map(int, line.split())
            if start <= self.uid + 1 < start + count:
                self.uid += 1
                break

    def acl(self, path):
        return subprocess.check_output(["getfacl", "-cpn", str(path)], text=True)

    def set_acl(self, path, value):
        subprocess.run(["setfacl", "-m", value, str(path)], check=True)

    def repair(self):
        migration.repair_server_acls(str(self.root), self.uid)

    def test_real_masked_access_and_default_acl_remain_restricted(self):
        file = self.root / "data"
        file.write_bytes(b"private data")
        os.chmod(file, 0o600)
        self.set_acl(file, "g::rw-,m::---")
        self.set_acl(self.root, "d:u::rwx,d:g::rwx,d:m::---,d:o::---")
        owner = (file.stat().st_uid, file.stat().st_gid)
        self.repair()
        self.assertIn("group::---", self.acl(file))
        self.assertIn("other::---", self.acl(file))
        self.assertIn("default:group::---", self.acl(self.root))
        self.assertIn(f"default:user:{self.uid}:rwx", self.acl(self.root))
        if self.uid != os.getuid():
            self.assertIn(f"user:{self.uid}:rw-", self.acl(file))
        self.assertEqual((file.stat().st_uid, file.stat().st_gid), owner)
        self.assertEqual(file.read_bytes(), b"private data")
        before = (self.acl(file), self.acl(self.root), file.stat().st_ctime_ns)
        self.repair()
        self.assertEqual(
            (self.acl(file), self.acl(self.root), file.stat().st_ctime_ns), before
        )

    def test_new_default_grants_account_without_exposing_new_files(self):
        os.chmod(self.root, 0o755)
        self.repair()
        previous = os.umask(0o077)
        try:
            child = self.root / "new-directory"
            child.mkdir(mode=0o777)
            file = child / "new-data"
            file.write_text("fixture")
        finally:
            os.umask(previous)
        acl = self.acl(file)
        self.assertIn(f"user:{self.uid}:rwx\t#effective:rw-", acl)
        self.assertIn("group::---", acl)
        self.assertIn("other::---", acl)
        self.assertFalse(file.stat().st_mode & 0o111)

    def test_symlinks_and_special_files_are_not_modified(self):
        outside = self.base / "outside"
        outside.mkdir()
        victim = outside / "private"
        victim.write_text("fixture")
        (self.root / "directory-link").symlink_to(outside)
        (self.root / "file-link").symlink_to(victim)
        fifo = self.root / "pipe"
        os.mkfifo(fifo, 0o600)
        before = (self.acl(outside), self.acl(victim), fifo.lstat().st_mode)
        self.repair()
        self.assertEqual(
            (self.acl(outside), self.acl(victim), fifo.lstat().st_mode), before
        )
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_existing_hardlink_fails_without_changing_external_inode(self):
        victim = self.base / "private"
        victim.write_text("fixture")
        os.link(victim, self.root / "hardlink")
        before = (self.acl(victim), victim.stat().st_ctime_ns)
        with self.assertRaisesRegex(migration.AclMigrationError, "multiply linked"):
            self.repair()
        self.assertEqual((self.acl(victim), victim.stat().st_ctime_ns), before)

    def test_root_and_ancestor_symlinks_are_rejected(self):
        link = self.base / "redirect"
        link.symlink_to(self.root)
        child = self.root / "child"
        child.mkdir()
        before = (self.acl(self.root), self.acl(child))
        for path in (link, link / "child"):
            with self.subTest(path=path), self.assertRaises(OSError):
                migration.repair_server_acls(str(path), self.uid)
        self.assertEqual((self.acl(self.root), self.acl(child)), before)

    def test_path_swapped_after_selection_cannot_redirect_acl_write(self):
        selected = self.root / "data"
        selected.write_text("selected inode")
        victim = self.base / "private"
        victim.write_text("external inode")
        before = (self.acl(victim), victim.stat().st_ctime_ns)
        real_open = os.open
        swapped = False

        def swap(path, flags, *args, **kwargs):
            nonlocal swapped
            if str(path).startswith("/proc/self/fd/") and not swapped:
                swapped = True
                selected.rename(self.root / "original")
                selected.symlink_to(victim)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(migration.os, "open", side_effect=swap):
            self.repair()
        self.assertTrue(swapped)
        self.assertEqual((self.acl(victim), victim.stat().st_ctime_ns), before)

    def test_hardlink_added_after_selection_is_rejected_before_write(self):
        selected = self.root / "data"
        selected.write_text("fixture")
        before = self.acl(selected)
        real_open = os.open

        def link(path, flags, *args, **kwargs):
            if str(path).startswith("/proc/self/fd/"):
                os.link(selected, self.base / "outside-link")
            return real_open(path, flags, *args, **kwargs)

        with patch.object(migration.os, "open", side_effect=link):
            with self.assertRaisesRegex(migration.AclMigrationError, "multiply linked"):
                self.repair()
        self.assertEqual(self.acl(selected), before)
