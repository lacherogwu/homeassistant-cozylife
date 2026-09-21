"""Talking to a CozyLife device directly, over its own TCP listener.

This is the fast path: no vendor round-trip, works with the internet down.
It is also the path with the firmware defect this integration exists to
work around -- the listener stops accepting connections after a few days of
uptime, recoverable only by power-cycling the device. When that happens
every call here raises :class:`TransportError` and the caller is expected
to fail over to the cloud relay.

The connection is held open and reused across calls. The leading suspect
for the defect is a per-connection leak in the device's firmware, and a
fresh socket per poll is thousands of connections a day where reuse is a
handful. An earlier A/B test is sometimes cited as showing connection
strategy does not matter, but it compared eight queries on a freshly booted
device and never ran to failure, so it cannot speak to what causes one.
Reuse is the cheaper hypothesis and costs nothing if it is wrong.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import protocol, wire
from .errors import TransportError

DEFAULT_PORT = 5555
DEFAULT_TIMEOUT = 5.0

_LOGGER = logging.getLogger(__name__)


class LocalTransport:
    """Synchronous client for one device's local listener.

    Calls serialise on a lock: a poll and a button press can arrive
    together, and two frames written to one socket at once would cross
    their replies.
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
        self._conn: wire.LineSocket | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        """Short label for logging which path served a request."""

        return "local"

    def __repr__(self) -> str:
        return f"LocalTransport({self._host}:{self._port})"

    def close(self) -> None:
        """Release the held connection, if any."""

        with self._lock:
            self._disconnect()

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

    def _disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _exchange(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Send one frame and return the reply carrying the same ``sn``.

        A held connection is replaced only when it is known the frame has
        not gone out -- the peer had hung up, or the write itself failed.
        Once a frame is on the wire there is no retry, because a command
        that may already have reached the device must not be sent twice.
        """

        packet = protocol.encode(frame)

        with self._lock:
            if self._conn is not None:
                if self._conn.peer_has_closed():
                    _LOGGER.debug("%r: device hung up, reconnecting", self)
                    self._disconnect()
                else:
                    try:
                        self._conn.send_line(packet)
                    except TransportError:
                        _LOGGER.debug("%r: stale connection, reconnecting", self)
                        self._disconnect()
                    else:
                        return self._await_reply(frame)

            self._conn = wire.connect(self._host, self._port, self._timeout)
            try:
                self._conn.send_line(packet)
            except TransportError:
                self._disconnect()
                raise
            return self._await_reply(frame)

    def _await_reply(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Read until the reply matching this frame's ``sn`` arrives."""

        assert self._conn is not None

        deadline = time.monotonic() + self._timeout
        for line in self._conn.lines(deadline):
            reply = protocol.decode(line)
            if reply is None:
                continue
            # Unsolicited cmd:10 reports interleave with real replies;
            # only the matching sn is this call's answer.
            if reply.get("sn") == frame["sn"]:
                return reply
            _LOGGER.debug("%r: ignoring unrelated frame %s", self, reply)

        # Nothing came back: either the listener has stopped answering, or
        # it hung up mid-exchange. Drop the connection so the next call
        # starts clean rather than inheriting a half-dead socket.
        self._disconnect()
        raise TransportError(
            f"{self!r} did not answer within {self._timeout}s "
            "(listener may have stopped responding)"
        )
