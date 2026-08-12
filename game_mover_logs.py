"""Safe bounded readers for persistent Minecraft log files."""

import os
import stat


class WorkloadLogError(ValueError):
    pass


MINECRAFT_LOG_WINDOW_BYTES = 512 * 1024
MINECRAFT_LOG_OUTPUT_BYTES = 256 * 1024


def read_minecraft_latest_log(data_directory: str, tail: int) -> dict | None:
    """Return a bounded tail of data/logs/latest.log, or None when it is absent."""
    root = os.path.realpath(str(data_directory or ""))
    if not root or not os.path.isdir(root):
        raise WorkloadLogError("Datový adresář Minecraft serveru neexistuje")
    logs_directory = os.path.join(root, "logs")
    path = os.path.join(logs_directory, "latest.log")
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        raise WorkloadLogError("Minecraft latest.log musí být běžný soubor v datovém adresáři")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise WorkloadLogError("Minecraft latest.log musí být běžný soubor")
            size = opened.st_size
            offset = max(0, size - MINECRAFT_LOG_WINDOW_BYTES)
            os.lseek(descriptor, offset, os.SEEK_SET)
            raw = os.read(descriptor, MINECRAFT_LOG_WINDOW_BYTES)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise WorkloadLogError(f"Minecraft latest.log nelze přečíst: {error}") from error
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if offset and lines:
        lines = lines[1:]
    selected = lines[-tail:]
    output = "\n".join(selected)
    encoded = output.encode("utf-8", errors="replace")
    truncated = bool(offset)
    if len(encoded) > MINECRAFT_LOG_OUTPUT_BYTES:
        output = encoded[-MINECRAFT_LOG_OUTPUT_BYTES:].decode("utf-8", errors="replace")
        output = "[Starší část výpisu byla zkrácena]\n" + output
        truncated = True
    return {"output": output, "truncated": truncated, "source": "minecraft-file"}
