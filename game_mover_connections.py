LOCAL_API_URL = "http://127.0.0.1:5000"
DEFAULT_SSH_TUNNEL_PORT = 5500
HOST_MANAGEMENT_MODES = frozenset(("server", "ssh_tunnel"))


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
