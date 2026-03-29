"""Minimal QMP client helpers."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from .errors import AppError


def qmp_execute(socket_path: Path, command: str) -> None:
    if not socket_path.exists():
        raise AppError(f"QMP socket not found: {socket_path}")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(socket_path))
        _recv_json(sock)
        _send_json(sock, {"execute": "qmp_capabilities"})
        _recv_json(sock)
        _send_json(sock, {"execute": command})
        _recv_json(sock)


def _recv_json(sock: socket.socket) -> dict:
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    if not data:
        return {}
    return json.loads(data.decode("utf-8").strip())


def _send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
