"""Single-threaded shared resource.

``POST /work`` reads a fixed-size body.  The handler blocks in ``read`` until
the client sends the last byte, so a slow client occupies the only worker.  A
concurrent ``GET /ping`` therefore queues behind it, and the queued request's
latency is the signal the receiver measures.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import socket
import socketserver
import time
from http.server import BaseHTTPRequestHandler
from typing import cast


class ChannelServer(socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], body_bytes: int, timeout_s: float = 5) -> None:
        if body_bytes <= 0 or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("body size and socket timeout must be positive and finite")
        self.body_bytes = body_bytes
        self.timeout_s = timeout_s
        super().__init__(address, ChannelHandler)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(self.timeout_s)
        return connection, address


class ChannelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "timing-channel-resource/0.1"
    sys_version = ""

    @property
    def channel_server(self) -> ChannelServer:
        return cast(ChannelServer, self.server)

    def log_message(self, fmt: str, *args: object) -> None:
        # The per-request access log would be dominated by /ping samples.
        # Structured /work records are emitted by do_POST instead.
        return

    def _empty_response(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/ping":
            self._empty_response(404)
            return
        self._empty_response(204)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/work":
            self._empty_response(404)
            return
        try:
            declared = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._empty_response(400)
            return
        if declared != self.channel_server.body_bytes:
            self._empty_response(400)
            return

        started_ms = time.monotonic() * 1000.0
        try:
            body = self.rfile.read(declared)
        except TimeoutError:
            self._empty_response(408)
            return
        ended_ms = time.monotonic() * 1000.0
        if len(body) != declared:
            self._empty_response(400)
            return

        record = {
            "event": "work",
            "path": self.path,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "t_start_ms": round(started_ms, 3),
            "t_end_ms": round(ended_ms, 3),
            "read_ms": round(ended_ms - started_ms, 3),
        }
        print(json.dumps(record, sort_keys=True), flush=True)
        self._empty_response(204)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shared serialized resource")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CHANNEL_PORT", "8080")))
    parser.add_argument(
        "--timeout", type=float, default=float(os.environ.get("CHANNEL_TIMEOUT_S", "5"))
    )
    parser.add_argument(
        "--body-bytes", type=int, default=int(os.environ.get("CHANNEL_BODY_BYTES", "64"))
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with ChannelServer((args.host, args.port), args.body_bytes, args.timeout) as server:
        print(
            json.dumps(
                {
                    "event": "startup",
                    "role": "resource",
                    "bind": f"{args.host}:{args.port}",
                    "body_bytes": args.body_bytes,
                    "workers": 1,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
