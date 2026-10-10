"""The saved SSH servers (D122a), kept in `remote/servers.json` in the config folder: a name, a
host, a port and a user name each, never a secret. Saga is the first."""

import json
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from chembook3d.settings import config_dir

SERVERS_FILE = "servers.json"
# A69: Saga's round-robin login name; a fixed login node can be set instead.
SAGA = {"id": "saga", "name": "Saga", "host": "saga.sigma2.no", "port": 22, "username": ""}
HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9A-Fa-f:.]{2,45}$")
USERNAME = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._@-]{0,63}$")
SERVER_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_SERVERS = 20


class ServerError(ValueError):
    pass


@dataclass
class Server:
    id: str
    name: str
    host: str
    port: int = 22
    username: str = ""


def folder() -> Path:
    return config_dir() / "remote"


def _path() -> Path:
    return folder() / SERVERS_FILE


def clean(data: object, known_ids: set[str] | None = None) -> Server:
    """A server from what the page sent, or ServerError saying what is wrong. The user name may
    still be empty (asked before logging in)."""
    if not isinstance(data, dict):
        raise ServerError("A server must have a name, a host and a port.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise ServerError("Give the server a name of at most 40 characters.")
    host = str(data.get("host") or "").strip()
    if not HOST.match(host):
        raise ServerError(f"“{host}” is not a host name such as saga.sigma2.no.")
    port = data.get("port", 22)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ServerError("The port must be a number from 1 to 65535.")
    username = str(data.get("username") or "").strip()
    if username and not USERNAME.match(username):
        raise ServerError(f"“{username}” is not a user name the app can use.")
    server_id = data.get("id")
    if not isinstance(server_id, str) or not SERVER_ID.match(server_id):
        server_id = None
    if server_id is None or (known_ids is not None and server_id in known_ids):
        server_id = secrets.token_hex(4)
    return Server(id=server_id, name=name, host=host, port=port, username=username)


def load() -> list[Server]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [Server(**SAGA)]
    servers: list[Server] = []
    for item in data.get("servers", []) if isinstance(data, dict) else []:
        try:
            servers.append(clean(item, {s.id for s in servers}))
        except ServerError:
            continue
    return servers


def save(servers: list[Server]) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    text = json.dumps({"servers": [asdict(s) for s in servers]}, indent=2) + "\n"
    _path().write_text(text, encoding="utf-8")


def replace(items: object) -> list[Server]:
    """Save the list the page sent, in its order; names must differ."""
    if not isinstance(items, list) or len(items) > MAX_SERVERS:
        raise ServerError(f"Keep at most {MAX_SERVERS} servers.")
    servers: list[Server] = []
    for item in items:
        server = clean(item, {s.id for s in servers})
        if any(s.name.lower() == server.name.lower() for s in servers):
            raise ServerError(f"Two servers are named “{server.name}”.")
        servers.append(server)
    save(servers)
    return servers


def find(server_id: str) -> Server | None:
    return next((s for s in load() if s.id == server_id), None)


def origin_path(server: Server, path: str) -> str:
    """Where a file on a server came from, as an import records it (D122e): saga:/cluster/…"""
    return re.sub(r"\s+", "-", server.name.strip().lower()) + ":" + path
