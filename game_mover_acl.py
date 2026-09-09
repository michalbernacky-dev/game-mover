"""Deployment-only migration of legacy managed server ACLs on Linux.

Uses the Linux POSIX ACL xattr ABI (include/uapi/linux/posix_acl_xattr.h).
No pathname is used for ACL writes, and this helper is not a broker action.
"""

import errno
import os
import pwd
import stat
import struct


USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER = 1, 2, 4, 8, 16, 32
UNDEFINED = 0xFFFFFFFF
ACCESS = "system.posix_acl_access"
DEFAULT = "system.posix_acl_default"
SERVER_ROOT = "/var/lib/game-platform/servers"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


class AclMigrationError(RuntimeError):
    pass


def _read_acl(fd, attribute):
    try:
        value = os.getxattr(fd, attribute)
    except OSError as error:
        if error.errno == errno.ENODATA:
            return None
        raise
    if (
        len(value) < 4
        or (len(value) - 4) % 8
        or struct.unpack_from("<I", value)[0] != 2
    ):
        raise AclMigrationError("Unsupported POSIX ACL encoding")
    return {
        (tag, identity): rights
        for tag, rights, identity in struct.iter_unpack("<HHI", value[4:])
    }


def _write_acl(fd, attribute, entries):
    value = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, rights, identity)
        for (tag, identity), rights in sorted(entries.items())
    )
    os.setxattr(fd, attribute, value)


def _mode_acl(mode):
    return {
        (USER_OBJ, UNDEFINED): (mode >> 6) & 7,
        (GROUP_OBJ, UNDEFINED): (mode >> 3) & 7,
        (OTHER, UNDEFINED): mode & 7,
    }


def grant_access(entries, uid, rights, *, owner_uid=None):
    """Expand only the target's rights; materialize other entries' old mask."""
    result = dict(entries)
    old_mask = result.get((MASK, UNDEFINED), 7)
    for key in result:
        if key[0] in (USER, GROUP_OBJ, GROUP):
            result[key] &= old_mask
    # The owning user is checked before named-user entries by the kernel.
    if owner_uid == uid:
        result[USER_OBJ, UNDEFINED] |= rights
    else:
        result[USER, uid] = result.get((USER, uid), 0) | rights
    mask = 0
    for (tag, _identity), permissions in result.items():
        if tag in (USER, GROUP_OBJ, GROUP):
            mask |= permissions
    if any(tag in (USER, GROUP, MASK) for tag, _identity in result):
        result[MASK, UNDEFINED] = mask
    return result


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


def _repair_inode(fd, uid):
    metadata = os.fstat(fd)
    directory = stat.S_ISDIR(metadata.st_mode)
    if not directory and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
        raise AclMigrationError(
            "Refusing ACL migration of a non-regular or multiply linked file"
        )
    access = _read_acl(fd, ACCESS) or _mode_acl(metadata.st_mode)
    # stat's execute bits reflect the effective ACL mask, not masked-out entries.
    rights = 7 if directory or metadata.st_mode & 0o111 else 6
    updated = grant_access(access, uid, rights, owner_uid=metadata.st_uid)
    # Detect concurrent ownership, mode, ACL, or hardlink changes while reading.
    current = os.fstat(fd)
    if (current.st_ctime_ns, current.st_nlink) != (
        metadata.st_ctime_ns,
        metadata.st_nlink,
    ):
        raise AclMigrationError(
            "Server data changed during ACL migration; retry while writers are stopped"
        )
    if updated != access:
        _write_acl(fd, ACCESS, updated)
    if directory:
        default = _read_acl(fd, DEFAULT)
        # A new default ACL bypasses umask. Do not copy directory group/other
        # permissions into future files that previously could have been private.
        baseline = default if default is not None else _mode_acl(0o700)
        updated_default = grant_access(baseline, uid, 7)
        if updated_default != default:
            _write_acl(fd, DEFAULT, updated_default)


def _walk(fd, uid, device, seen):
    metadata = os.fstat(fd)
    if metadata.st_dev != device or _identity(metadata) in seen:
        raise AclMigrationError("Refusing filesystem boundary or repeated directory")
    seen.add(_identity(metadata))
    _repair_inode(fd, uid)
    for name in os.listdir(fd):
        # O_PATH cannot open a device/FIFO for I/O, even after a concurrent swap.
        selected = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        try:
            child = os.fstat(selected)
            if stat.S_ISLNK(child.st_mode):
                continue
            if not (stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode)):
                continue
            if child.st_dev != device or (
                stat.S_ISREG(child.st_mode) and child.st_nlink != 1
            ):
                raise AclMigrationError(
                    "Refusing filesystem boundary or multiply linked file"
                )
            # Reopen the selected inode, not its mutable directory entry.
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
            if stat.S_ISDIR(child.st_mode):
                flags |= os.O_DIRECTORY
            opened = os.open(f"/proc/self/fd/{selected}", flags)
            try:
                if _identity(os.fstat(opened)) != _identity(child):
                    raise AclMigrationError("Server inode changed during ACL migration")
                if stat.S_ISDIR(child.st_mode):
                    _walk(opened, uid, device, seen)
                else:
                    _repair_inode(opened, uid)
            finally:
                os.close(opened)
        finally:
            os.close(selected)


def _open_root(path, *, create_owner=None):
    if not os.path.isabs(path) or ".." in path.split("/"):
        raise AclMigrationError(
            "Managed server root must be an absolute, non-traversing path"
        )
    parts = [part for part in path.split("/") if part and part != "."]
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for index, part in enumerate(parts):
            created = False
            if index == len(parts) - 1 and create_owner is not None:
                try:
                    os.mkdir(part, 0o750, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if created:
                os.fchown(descriptor, *create_owner)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def repair_server_acls(path, uid, *, create_owner=None):
    """Repair selected inodes without following redirected paths.

    Migration is idempotent, not transactional; an error may leave earlier
    safe entries repaired. Quiesce writers for a consistent whole-tree result.
    Existing hardlinks are refused, not silently granted access or unlinked.
    """
    descriptor = _open_root(path, create_owner=create_owner)
    try:
        _walk(descriptor, uid, os.fstat(descriptor).st_dev, set())
    finally:
        os.close(descriptor)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Managed ACL migration must run as root during deployment")
    account = pwd.getpwnam("gameplatform")
    try:
        repair_server_acls(
            SERVER_ROOT, account.pw_uid, create_owner=(account.pw_uid, account.pw_gid)
        )
    except (OSError, AclMigrationError) as error:
        raise SystemExit(f"Managed server ACL migration failed: {error}") from error


if __name__ == "__main__":
    main()
