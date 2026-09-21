"""CRLF line framing over a plain TCP socket.

Both transports speak a line protocol -- the device's own listener carries
JSON frames directly, the cloud relay carries them inside its own
``key=value&...`` lines -- so the buffering lives here once.
"""

from __future__ import annotations

import contextlib
import socket
import time
from collections.abc import Iterator
from typing import Self

from .errors import TransportError

TERMINATOR = b"\r\n"
_CHUNK = 4096


class LineSocket:
    """A connected socket that reads whole CRLF-terminated lines.

    The read buffer persists across calls, because a single TCP segment can
    carry several frames and a frame can straddle two segments. Treating one
    ``recv`` as one message is the classic bug here; the device really does
    coalesce its writes.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def send_line(self, raw: bytes) -> None:
        """Send an already-terminated line."""

        try:
            self._sock.sendall(raw)
        except OSError as err:
            raise TransportError(f"failed sending to {self._peer()}: {err}") from err

    def lines(self, deadline: float) -> Iterator[bytes]:
        """Yield lines as they arrive, until ``deadline`` (a monotonic time).

        Stops yielding at the deadline or when the peer closes, rather than
        raising -- "nothing more arrived" is for the caller to interpret,
        since what counts as an answer differs per transport.
        """

        while True:
            while TERMINATOR in self._buffer:
                line, _, self._buffer = self._buffer.partition(TERMINATOR)
                if line:
                    yield line

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(_CHUNK)
            except TimeoutError:
                return
            except OSError as err:
                raise TransportError(f"failed reading from {self._peer()}: {err}") from err

            if not chunk:
                return
            self._buffer += chunk

    def peer_has_closed(self) -> bool:
        """Whether the far end has hung up, checked without blocking.

        Reusing a connection means inheriting the half-open problem: a
        device that hung up while we were idle leaves a socket that still
        accepts a write, and only reveals itself when the reply never comes.
        A non-blocking peek distinguishes that up front, so a stale
        connection can be replaced *before* a frame is sent rather than
        after -- which matters, because a frame that may already have
        reached the device must never be sent twice.
        """

        if self._buffer:
            return False

        try:
            self._sock.setblocking(False)
            peeked = self._sock.recv(_CHUNK, socket.MSG_PEEK)
        except (BlockingIOError, InterruptedError):
            return False  # nothing to read, connection still open
        except OSError:
            return True
        finally:
            with contextlib.suppress(OSError):
                self._sock.setblocking(True)

        return peeked == b""

    def _peer(self) -> str:
        try:
            host, port = self._sock.getpeername()[:2]
        except OSError:
            return "peer"
        return f"{host}:{port}"

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def connect(host: str, port: int, timeout: float) -> LineSocket:
    """Open a line-oriented connection, or raise :class:`TransportError`."""

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as err:
        raise TransportError(f"cannot connect to {host}:{port}: {err}") from err
    return LineSocket(sock)
