"""In-process fake servers for the two transports.

Every test in this suite runs against these. Nothing here ever opens a
socket to a real CozyLife device or to CozyLife's cloud -- see
WRITE_SAFETY.md for why that rule is absolute for the write path.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable


class FakeLineServer:
    """A TCP server that speaks the CRLF line protocol both transports use.

    ``handler`` is called with each received line (bytes, terminator
    stripped) and returns an iterable of raw byte strings to send back.
    Returning nothing simulates a device that accepts a connection but
    never answers -- the exact failure mode this project exists to survive.
    """

    def __init__(self, handler: Callable[[bytes], list[bytes]]) -> None:
        self._handler = handler
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._stop = threading.Event()
        self.received: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def host(self) -> str:
        return self._socket.getsockname()[0]

    @property
    def port(self) -> int:
        return self._socket.getsockname()[1]

    def _serve(self) -> None:
        self._socket.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buffer = b""
        conn.settimeout(0.2)
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except (TimeoutError, OSError):
                    continue
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n" in buffer:
                    line, _, buffer = buffer.partition(b"\r\n")
                    if not line:
                        continue
                    self.received.append(line)
                    for reply in self._handler(line):
                        conn.sendall(reply)
        finally:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()

    def __enter__(self) -> "FakeLineServer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def unused_port() -> int:
    """Return a port number nothing is listening on, for refusal tests."""

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class FakeHttpServer:
    """A stand-in for CozyLife's cloud HTTP API.

    Records every request so tests can assert on the exact field names,
    encoding and headers the real API turned out to require -- several of
    which are non-obvious and cost real debugging time to discover.
    """

    def __init__(self, routes: dict[str, dict]) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import json as _json
        import urllib.parse as _urlparse

        recorded: list[dict] = []
        self.requests = recorded

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # keep pytest output clean
                pass

            def _respond(self, path: str, query: str, body: str, headers) -> None:
                recorded.append(
                    {
                        "path": path,
                        "query": dict(_urlparse.parse_qsl(query)),
                        "form": dict(_urlparse.parse_qsl(body)),
                        "headers": dict(headers),
                        "body": body,
                    }
                )
                payload = routes.get(path, {"ret": "404", "desc": "no such route"})
                encoded = _json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                parsed = _urlparse.urlparse(self.path)
                self._respond(parsed.path, parsed.query, body, self.headers)

            def do_GET(self):  # noqa: N802
                parsed = _urlparse.urlparse(self.path)
                self._respond(parsed.path, parsed.query, "", self.headers)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakeHttpServer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
