"""Talking to a CozyLife device directly, over its own TCP listener.

This is the fast path: no vendor round-trip, works with the internet down.
It is also the path with the firmware defect this integration exists to
work around -- the listener leaks and stops accepting connections after a
few days of uptime, recoverable only by power-cycling the device. When that
happens every call here raises :class:`TransportError` and the caller is
expected to fail over to the cloud relay.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import protocol, wire
from .errors import TransportError

DEFAULT_PORT = 5555
DEFAULT_TIMEOUT = 5.0

_LOGGER = logging.getLogger(__name__)


class LocalTransport:
    """Synchronous client for one device's local listener.

    Every call opens its own connection and closes it again. A persistent
    socket was measured against this and scored identically (8/8 either way
    right after a power-cycle); since the failure mode is the listener dying
    underneath us, holding a socket open only creates a stale descriptor to
    detect and clean up. Stateless calls also mean no lock is needed.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Short label for logging which path served a request."""

        return "local"

    def __repr__(self) -> str:
        return f"LocalTransport({self._host}:{self._port})"

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]:
        """Read the device's datapoints. Never changes state."""

        reply = self._exchange(protocol.query_frame(attrs))
        message = reply.get("msg")
        data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(data, dict):
            raise TransportError(f"{self!r} answered without a data map: {reply}")
        return data

    def control(self, payload: dict[Any, int]) -> bool:
        """Write datapoints. Drives real hardware -- see WRITE_SAFETY.md.

        Returns whether the device accepted the command. A refusal is a
        ``False``, not an exception: the device was reachable, so there is
        nothing for the cloud transport to do better.
        """

        reply = self._exchange(protocol.set_frame(payload))
        return reply.get("res") == 0

    def _exchange(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Send one frame and return the reply carrying the same ``sn``."""

        with wire.connect(self._host, self._port, self._timeout) as conn:
            conn.send_line(protocol.encode(frame))

            deadline = time.monotonic() + self._timeout
            for line in conn.lines(deadline):
                reply = protocol.decode(line)
                if reply is None:
                    continue
                # Unsolicited cmd:10 reports interleave with real replies;
                # only the matching sn is this call's answer.
                if reply.get("sn") == frame["sn"]:
                    return reply
                _LOGGER.debug("%r: ignoring unrelated frame %s", self, reply)

        raise TransportError(
            f"{self!r} did not answer within {self._timeout}s "
            "(listener may have stopped responding)"
        )
