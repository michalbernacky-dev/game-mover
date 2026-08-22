import os
import pwd
import tempfile
import unittest

from game_mover_users import interactive_usernames, login_uid_range


def passwd_entry(name, uid, home=None, shell="/bin/bash", gecos=""):
    return pwd.struct_passwd((
        name, "x", uid, uid, gecos, home or f"/home/{name}", shell,
    ))


class InteractiveUsersTest(unittest.TestCase):
    def test_uses_configured_login_uid_range(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("UID_MIN 1500\nUID_MAX 2500\n")
            path = handle.name
        try:
            self.assertEqual(login_uid_range(path), (1500, 2500))
        finally:
            os.unlink(path)

    def test_filters_orphaned_homes_system_users_and_service_accounts(self):
        entries = [
            passwd_entry("alice", 1000),
            passwd_entry("bob", 1001),
            passwd_entry("carol", 1002),
            passwd_entry("dave", 1003),
            passwd_entry("satisfactory-srv", 1004),
            passwd_entry("daemon", 50, shell="/usr/sbin/nologin"),
            passwd_entry("remote", 1005, home="/srv/remote"),
        ]
        result = interactive_usernames(
            entries, home_exists=lambda _path: True, uid_range=(1000, 60000),
        )
        self.assertEqual(result, ["dave", "alice", "bob", "carol"])


if __name__ == "__main__":
    unittest.main()
