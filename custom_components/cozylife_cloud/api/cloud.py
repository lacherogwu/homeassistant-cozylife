"""Talking to a CozyLife device through CozyLife's cloud relay.

This is the fallback path, and the reason this integration exists: the
relay keeps working while the device's local TCP listener is dead, exactly
as the phone app does.

The relay speaks its own CRLF line protocol, one ``key=value`` pair per
field, wrapping the ordinary device frame as a url-encoded ``message``::

    cmd=subscribe&topic=device_<id>&device_id=<id>&device_key=<key>
    cmd=publish&topic=control_<id>&device_id=<id>&device_key=<key>&message=<frame>

Two topics exist per device: ``control_<id>`` carries commands to it and
``device_<id>`` carries reports back. A client MUST subscribe to the report
topic before publishing, or the relay has nowhere to route the reply and it
simply never arrives -- the one genuinely non-obvious step in this protocol.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from . import protocol, wire
from .errors import TransportError

DEFAULT_PORT = 8898
DEFAULT_TIMEOUT = 10.0

_LOGGER = logging.getLogger(__name__)


def _parse_line(line: bytes) -> dict[str, str]:
    """Split a relay line into its fields, leaving values url-encoded.

    Deliberately not ``parse_qs``: that applies form decoding, which turns a
    literal ``+`` inside the JSON payload into a space.
    """

    fields: dict[str, str] = {}
    for part in line.decode("utf-8", "replace").split("&"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key] = value
    return fields


class CloudRelayTransport:
    """Synchronous client for one device, via its assigned cloud relay.

    ``host``/``port`` are per-device and come from the account's device
    list -- they are not a constant. A device registered on one relay port
    will happily accept a publish on another and deliver it to nobody
    (``num=0``), so the port must be read from the account, never assumed.
    """

    def __init__(
        self,
        device_id: str,
        device_key: str,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._device_id = device_id
        self._device_key = device_key
        self._host = host
        self._port = port
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Short label for logging which path served a request."""

        return "cloud"

    def __repr__(self) -> str:
        # device_key is deliberately absent: this string reaches logs.
        return f"CloudRelayTransport({self._device_id} via {self._host}:{self._port})"

    @property
    def report_topic(self) -> str:
        return f"device_{self._device_id}"

    @property
    def control_topic(self) -> str:
        return f"control_{self._device_id}"

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]:
        """Read the device's datapoints. Never changes state."""

        reply = self._exchange(protocol.query_frame(attrs))
        message = reply.get("msg")
        data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(data, dict):
            raise TransportError(f"{self!r} reported without a data map: {reply}")
        return data

    def control(self, payload: dict[Any, int]) -> bool:
        """Write datapoints. Drives real hardware -- see WRITE_SAFETY.md."""

        reply = self._exchange(protocol.set_frame(payload))
        return reply.get("res") == 0

    def _compose(self, **fields: str) -> bytes:
        fields.setdefault("device_id", self._device_id)
        fields.setdefault("device_key", self._device_key)
        line = "&".join(f"{key}={value}" for key, value in fields.items())
        return line.encode() + wire.TERMINATOR

    def _exchange(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Subscribe, publish one frame, and wait for the device's report."""

        deadline = time.monotonic() + self._timeout

        with wire.connect(self._host, self._port, self._timeout) as conn:
            conn.send_line(self._compose(cmd="subscribe", topic=self.report_topic))

            message = urllib.parse.quote(
                protocol.encode(frame)[: -len(wire.TERMINATOR)].decode(), safe=""
            )
            conn.send_line(
                self._compose(
                    cmd="publish", topic=self.control_topic, message=message
                )
            )

            for line in conn.lines(deadline):
                fields = _parse_line(line)

                if self._is_undelivered_ack(fields):
                    raise TransportError(
                        f"{self!r} accepted the command but reached no subscriber "
                        "(device offline, or registered on a different relay port)"
                    )

                reply = self._extract_report(fields)
                if reply is not None and reply.get("sn") == frame["sn"]:
                    return reply

        raise TransportError(
            f"{self!r} did not report back within {self._timeout}s"
        )

    def _is_undelivered_ack(self, fields: dict[str, str]) -> bool:
        """Detect the relay's own "delivered to nobody" receipt.

        The ack is a delivery receipt, not the device's answer: ``num`` is
        the count of live subscribers it reached. Zero means nothing got it.
        """

        return fields.get("cmd") == "publish" and fields.get("num") == "0"

    def _extract_report(self, fields: dict[str, str]) -> dict[str, Any] | None:
        """Pull the device frame out of a report line addressed to us."""

        if fields.get("topic") != self.report_topic:
            return None
        raw = fields.get("message")
        if raw is None:
            return None
        return protocol.decode(urllib.parse.unquote(raw))
