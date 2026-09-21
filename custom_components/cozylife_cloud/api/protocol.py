"""The CozyLife device frame, shared by the local and cloud transports.

Frames are compact JSON objects terminated by CRLF:

    {"pv":0,"cmd":2,"sn":"1758470400000","msg":{"attr":[0]}}

``cmd`` 2 queries, 3 sets. ``sn`` correlates a reply with its request; the
device echoes it back unchanged. The cloud relay carries this exact object
as the url-encoded ``message`` field of its own line protocol, so both
transports encode identically.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .wire import TERMINATOR

CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3

PROTOCOL_VERSION = 0

#: ``attr: [0]`` is the device's "everything you have" wildcard.
ATTR_ALL = 0

_last_sn = 0


def new_sn() -> str:
    """Return a unique sequence number for a new frame.

    Epoch milliseconds, matching what the phone app sends. Two frames built
    in the same millisecond would otherwise share an ``sn``, which would let
    one command's reply satisfy another's wait, so the counter is nudged
    forward to keep every value distinct within a process.
    """

    global _last_sn

    candidate = int(time.time() * 1000)
    if candidate <= _last_sn:
        candidate = _last_sn + 1
    _last_sn = candidate
    return str(candidate)


def query_frame(attrs: list[int] | None = None, *, sn: str | None = None) -> dict[str, Any]:
    """Build a read-only query frame. Never changes device state."""

    return {
        "pv": PROTOCOL_VERSION,
        "cmd": CMD_QUERY,
        "sn": sn or new_sn(),
        "msg": {"attr": list(attrs) if attrs else [ATTR_ALL]},
    }


def set_frame(payload: dict[Any, int], *, sn: str | None = None) -> dict[str, Any]:
    """Build a write frame. This drives real hardware -- see WRITE_SAFETY.md.

    ``data`` must be keyed by strings; callers naturally reach for ints, so
    the conversion happens here rather than at every call site.
    """

    data = {str(dpid): value for dpid, value in payload.items()}
    return {
        "pv": PROTOCOL_VERSION,
        "cmd": CMD_SET,
        "sn": sn or new_sn(),
        "msg": {"attr": [int(dpid) for dpid in data], "data": data},
    }


def encode(frame: dict[str, Any]) -> bytes:
    """Serialise a frame for the wire."""

    return json.dumps(frame, separators=(",", ":")).encode("utf-8") + TERMINATOR


def decode(raw: bytes | str) -> dict[str, Any] | None:
    """Parse one frame, or return ``None`` if it is not a usable frame.

    Returns ``None`` rather than raising: a malformed line from the relay is
    an ordinary event mid-poll, not an error worth unwinding a poll for.
    """

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    try:
        frame = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None

    return frame if isinstance(frame, dict) else None
