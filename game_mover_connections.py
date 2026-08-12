import ipaddress
import re


LOCAL_API_URL = "http://127.0.0.1:5000"
DEFAULT_SSH_TUNNEL_PORT = 5500
DEFAULT_SSH_PORT = 22
HOST_MANAGEMENT_MODES = frozenset(("server", "ssh_tunnel"))
SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}\$?$")
SSH_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


def normalize_app_mode(value):
    return value if value in ("client", "server", "ssh_tunnel") else "client"


def is_host_management_mode(value):
    return normalize_app_mode(value) in HOST_MANAGEMENT_MODES


def normalize_ssh_tunnel_port(port):
    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        return DEFAULT_SSH_TUNNEL_PORT
    if not 1 <= normalized_port <= 65535:
        return DEFAULT_SSH_TUNNEL_PORT
    return normalized_port


def normalize_ssh_port(port):
    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        return DEFAULT_SSH_PORT
    if not 1 <= normalized_port <= 65535:
        return DEFAULT_SSH_PORT
    return normalized_port


def connection_profile_host(address):
    value = str(address or "").strip()
    for prefix in ("http://", "https://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    if not value or any(character.isspace() for character in value):
        raise ValueError("Profil připojení nemá platnou adresu hostitele")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            raise ValueError("Neplatná IPv6 adresa v profilu")
        host = value[1:closing]
        suffix = value[closing + 1:]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            raise ValueError("Neplatná adresa nebo port profilu")
        return validate_ssh_host(host)
    if value.count(":") == 1:
        host, separator, port = value.rpartition(":")
        if separator and port.isdigit():
            value = host
    return validate_ssh_host(value)


def validate_ssh_host(host):
    value = str(host or "").strip()
    if not value or any(character in value for character in ("/", "@", "[", "]")):
        raise ValueError("Neplatný SSH hostitel")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if not SSH_HOST_RE.fullmatch(value):
        raise ValueError("Neplatný SSH hostitel")
    return value


def validate_ssh_user(username):
    value = str(username or "").strip()
    if not SSH_USER_RE.fullmatch(value):
        raise ValueError("Neplatné jméno SSH uživatele")
    return value


def managed_ssh_tunnel_arguments(host, username, local_port, ssh_port=DEFAULT_SSH_PORT):
    host = validate_ssh_host(host)
    username = validate_ssh_user(username)
    local_port = normalize_ssh_tunnel_port(local_port)
    ssh_port = normalize_ssh_port(ssh_port)
    return [
        "-F", "/dev/null",
        "-N", "-T",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ForwardAgent=no",
        "-o", "ForwardX11=no",
        "-o", "PermitLocalCommand=no",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-p", str(ssh_port),
        "-L", f"127.0.0.1:{local_port}:127.0.0.1:5000",
        f"{username}@{host}",
    ]


def ssh_tunnel_api_url(port=DEFAULT_SSH_TUNNEL_PORT):
    normalized_port = normalize_ssh_tunnel_port(port)
    return f"http://127.0.0.1:{normalized_port}"


def management_api_url(mode, tunnel_port=DEFAULT_SSH_TUNNEL_PORT):
    normalized_mode = normalize_app_mode(mode)
    if normalized_mode == "server":
        return LOCAL_API_URL
    if normalized_mode == "ssh_tunnel":
        return ssh_tunnel_api_url(tunnel_port)
    raise ValueError("Klientský režim nemá oprávnění ke správě hostitele")
