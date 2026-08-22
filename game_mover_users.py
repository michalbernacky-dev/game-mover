"""Discovery of real interactive users for user-facing administration controls."""

import os
import pwd
import re


NON_INTERACTIVE_SHELLS = frozenset((
    "/bin/false", "/bin/sync", "/sbin/halt", "/sbin/nologin",
    "/sbin/shutdown", "/usr/bin/false", "/usr/sbin/nologin",
))
SERVICE_ACCOUNT_RE = re.compile(
    r"(?:^|[-_])(?:bot|daemon|server|service|srv)$", re.IGNORECASE,
)


def login_uid_range(path="/etc/login.defs"):
    """Return the distro's regular-login UID range with safe Linux defaults."""
    limits = {"UID_MIN": 1000, "UID_MAX": 60000}
    try:
        with open(path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.split("#", 1)[0].strip()
                parts = line.split()
                if len(parts) == 2 and parts[0] in limits:
                    limits[parts[0]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return limits["UID_MIN"], limits["UID_MAX"]


def interactive_usernames(passwd_entries=None, home_exists=os.path.isdir, uid_range=None):
    """List live human login accounts, not orphaned homes or service identities."""
    entries = list(passwd_entries) if passwd_entries is not None else pwd.getpwall()
    uid_min, uid_max = uid_range or login_uid_range()
    users = []
    for entry in entries:
        username = entry.pw_name
        expected_home = f"/home/{username}"
        if not uid_min <= entry.pw_uid <= uid_max:
            continue
        if entry.pw_dir != expected_home or not home_exists(expected_home):
            continue
        if entry.pw_shell in NON_INTERACTIVE_SHELLS or not entry.pw_shell:
            continue
        if SERVICE_ACCOUNT_RE.search(username):
            continue
        users.append(username)
    return sorted(set(users), key=str.casefold)
