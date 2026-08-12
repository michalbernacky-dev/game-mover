"""Security policy catalog and persistent configuration normalization."""

SECURITY_CONFIG_VERSION = 1
POLICY_MODES = ("silent", "pam", "disabled")

SERVER_ACTION_DEFINITIONS = (
    {"id": "start", "label": "Spuštění", "default": "silent"},
    {"id": "stop", "label": "Vypnutí", "default": "silent"},
    {"id": "restart", "label": "Restart", "default": "silent"},
    {"id": "backup", "label": "Záloha", "default": "pam"},
)

GLOBAL_OPERATION_DEFINITIONS = (
    {
        "id": "server.registry",
        "label": "Registr serverů",
        "description": "Přidávání, adopce a změny registrovaných workloadů.",
        "default": "pam",
    },
    {
        "id": "backup.catalog",
        "label": "Katalog záloh",
        "description": "Načtení seznamu záloh použitelných pro obnovu.",
        "default": "pam",
    },
    {
        "id": "minecraft.install",
        "label": "Instalace a obnova Minecraftu",
        "description": "Vytvoření Podman serveru, stažení image a obnova dat.",
        "default": "pam",
    },
    {
        "id": "minecraft.properties",
        "label": "Nastavení Minecraft serveru",
        "description": "Čtení a validovaný zápis server.properties registrovaného Minecraft serveru.",
        "default": "pam",
    },
    {
        "id": "gate.config",
        "label": "Konfigurace Gate Lite",
        "description": "Změny základní konfigurace sdíleného ingressu.",
        "default": "pam",
    },
    {
        "id": "gate.routes",
        "label": "Směrování Gate Lite",
        "description": "Změny hostname tras k Minecraft serverům.",
        "default": "pam",
    },
    {
        "id": "gate.deploy",
        "label": "Nasazení Gate Lite",
        "description": "Stažení image a vytvoření proxy containeru.",
        "default": "pam",
    },
    {
        "id": "gate.lifecycle",
        "label": "Ovládání Gate Lite",
        "description": "Spuštění, vypnutí a restart proxy containeru.",
        "default": "pam",
    },
)

FIXED_OPERATION_DEFINITIONS = (
    {
        "id": "security.manage",
        "label": "Změny zabezpečení",
        "description": "Načtení a uložení tohoto registru oprávnění.",
        "policy": "pam",
    },
    {
        "id": "timekpr.manage",
        "label": "Správa Timekpr",
        "description": "Změny limitů a časových oken uživatelů.",
        "policy": "pam",
    },
)

SERVER_ACTION_IDS = tuple(item["id"] for item in SERVER_ACTION_DEFINITIONS)
GLOBAL_OPERATION_IDS = tuple(item["id"] for item in GLOBAL_OPERATION_DEFINITIONS)
SERVER_ACTION_DEFAULTS = {
    item["id"]: item["default"] for item in SERVER_ACTION_DEFINITIONS
}
GLOBAL_OPERATION_DEFAULTS = {
    item["id"]: item["default"] for item in GLOBAL_OPERATION_DEFINITIONS
}


def _valid_policy(value, default):
    return value if value in POLICY_MODES else default


def legacy_server_policies(server):
    permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
    legacy = str(server.get("control_auth", "silent")).strip().lower()
    if legacy not in ("silent", "pam"):
        legacy = "silent"
    defaults = dict(SERVER_ACTION_DEFAULTS)
    for action in ("start", "stop", "restart"):
        defaults[action] = legacy
    return {
        action: _valid_policy(permissions.get(action), defaults[action])
        for action in SERVER_ACTION_IDS
    }


def normalize_security_config(raw_config, servers):
    raw_config = raw_config if isinstance(raw_config, dict) else {}
    raw_global = raw_config.get("global") if isinstance(raw_config.get("global"), dict) else {}
    raw_servers = raw_config.get("servers") if isinstance(raw_config.get("servers"), dict) else {}
    normalized_servers = {}
    for server in servers:
        if not isinstance(server, dict) or not server.get("id"):
            continue
        server_id = str(server["id"])
        configured = raw_servers.get(server_id)
        configured = configured if isinstance(configured, dict) else {}
        defaults = legacy_server_policies(server)
        normalized_servers[server_id] = {
            action: _valid_policy(configured.get(action), defaults[action])
            for action in SERVER_ACTION_IDS
        }
    return {
        "version": SECURITY_CONFIG_VERSION,
        "global": {
            operation: _valid_policy(raw_global.get(operation), default)
            for operation, default in GLOBAL_OPERATION_DEFAULTS.items()
        },
        "servers": normalized_servers,
    }


def disabled_security_config(servers):
    """Return a complete fail-closed policy set for an unreadable config."""
    return normalize_security_config({
        "global": {
            operation: "disabled" for operation in GLOBAL_OPERATION_IDS
        },
        "servers": {
            str(server["id"]): {
                action: "disabled" for action in SERVER_ACTION_IDS
            }
            for server in servers
            if isinstance(server, dict) and server.get("id")
        },
    }, servers)


def validate_security_update(raw_config, servers):
    if not isinstance(raw_config, dict):
        raise ValueError("Neplatný registr zabezpečení")
    raw_global = raw_config.get("global")
    raw_servers = raw_config.get("servers")
    if not isinstance(raw_global, dict) or not isinstance(raw_servers, dict):
        raise ValueError("Registr musí obsahovat globální a serverové zásady")
    unknown_global = set(raw_global) - set(GLOBAL_OPERATION_IDS)
    if unknown_global:
        raise ValueError("Neznámá globální bezpečnostní operace")
    server_ids = {str(server.get("id")) for server in servers if server.get("id")}
    unknown_servers = set(raw_servers) - server_ids
    if unknown_servers:
        raise ValueError("Zásady obsahují neznámý server")
    for policy in raw_global.values():
        if policy not in POLICY_MODES:
            raise ValueError("Neplatná globální bezpečnostní zásada")
    for server_id, policies in raw_servers.items():
        if not isinstance(policies, dict):
            raise ValueError(f"Neplatné zásady serveru {server_id}")
        if set(policies) - set(SERVER_ACTION_IDS):
            raise ValueError("Neznámá serverová bezpečnostní akce")
        if any(policy not in POLICY_MODES for policy in policies.values()):
            raise ValueError("Neplatná serverová bezpečnostní zásada")
    return normalize_security_config(raw_config, servers)


def server_policy(config, server, action):
    if action not in SERVER_ACTION_IDS:
        return "disabled"
    server_id = str(server.get("id", ""))
    configured = config.get("servers", {}).get(server_id, {})
    return _valid_policy(configured.get(action), legacy_server_policies(server)[action])


def global_policy(config, operation):
    default = GLOBAL_OPERATION_DEFAULTS.get(operation)
    if default is None:
        return "disabled"
    return _valid_policy(config.get("global", {}).get(operation), default)


def public_security_payload(config, servers):
    names = {
        str(server.get("id")): str(server.get("name") or server.get("id"))
        for server in servers if server.get("id")
    }
    return {
        "version": SECURITY_CONFIG_VERSION,
        "modes": list(POLICY_MODES),
        "policies": config,
        "catalog": {
            "global": [dict(item) for item in GLOBAL_OPERATION_DEFINITIONS],
            "server_actions": [dict(item) for item in SERVER_ACTION_DEFINITIONS],
            "fixed": [dict(item) for item in FIXED_OPERATION_DEFINITIONS],
        },
        "servers": [
            {"id": server_id, "name": names.get(server_id, server_id)}
            for server_id in config.get("servers", {})
        ],
    }
