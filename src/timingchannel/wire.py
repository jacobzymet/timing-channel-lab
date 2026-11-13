"""Socket helpers shared by the sender, receiver and noise generator."""

from __future__ import annotations

import json
import math
import os
import socket
import time
from datetime import UTC, datetime
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def write_json_atomic(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _request_headers(host: str, port: int, method: str, path: str, length: int | None) -> bytes:
    headers = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "User-Agent: timing-channel-lab/0.1",
        "Connection: close",
    ]
    if length is not None:
        headers.append(f"Content-Length: {length}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")


def _expect_success(sock: socket.socket) -> None:
    with HTTPResponse(sock) as response:
        response.begin()
        if response.status != 204:
            raise OSError(f"resource returned HTTP {response.status}; expected 204")


def post_with_hold(
    host: str,
    port: int,
    body: bytes,
    hold_ms: float,
    timeout_s: float,
) -> float:
    """POST an identical body, but delay the body bytes by ``hold_ms``.

    The request line, headers and body are byte-for-byte identical for every
    symbol.  Only the moment the body finishes changes, which keeps the
    information out of the request contents.
    """
    request = _request_headers(host, port, "POST", "/work", len(body))
    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout_s) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout_s)
        sock.sendall(request)
        time.sleep(max(0.0, hold_ms) / 1000.0)
        sock.sendall(body)
        _expect_success(sock)
    return (time.perf_counter() - started) * 1000.0


def ping_once(host: str, port: int, timeout_s: float) -> tuple[float, bool]:
    """GET /ping over a fresh connection; returns (rtt_ms, timed_out)."""
    request = _request_headers(host, port, "GET", "/ping", None)
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(timeout_s)
            sock.sendall(request)
            _expect_success(sock)
        return (time.perf_counter() - started) * 1000.0, False
    except (OSError, HTTPException):
        return (time.perf_counter() - started) * 1000.0, True


def wait_for_resource(host: str, port: int, timeout_s: float = 30.0) -> None:
    """Block until the shared resource answers a normal /ping."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("startup timeout must be finite and positive")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        _, timed_out = ping_once(host, port, timeout_s=min(1.0, remaining))
        if not timed_out:
            return
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"resource {host}:{port} did not answer /ping")
