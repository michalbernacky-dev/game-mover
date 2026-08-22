"""Shared definition of directories that are support data, not games."""


EXCLUDE_PREFIXES = ("SteamLinuxRuntime", "Proton")
EXCLUDE_LIST = {
    "steam": ("Half-Life Dedicated Server",),
    "gog": (),
    "epic": (),
    "ubisoft": (),
    "rockstar": (),
}


def is_excluded_game(platform, name):
    """Return true when Mover and Tips should ignore this directory/title."""
    platform = str(platform).strip().lower()
    name_folded = str(name).strip().casefold()
    if any(name_folded.startswith(prefix.casefold()) for prefix in EXCLUDE_PREFIXES):
        return True
    return name_folded in {
        excluded.casefold() for excluded in EXCLUDE_LIST.get(platform, ())
    }

