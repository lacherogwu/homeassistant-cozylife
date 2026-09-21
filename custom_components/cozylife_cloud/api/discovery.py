"""Finding a CozyLife device's current address on the LAN.

The account's device list returns the device's assigned *relay* address,
never its local one, so the local transport needs this to know where to
connect.

Discovery deliberately runs over UDP. The device's responder on port 6095
keeps answering after its TCP listener has stopped accepting connections --
the exact failure this integration works around -- so a UDP probe can still
locate a device whose local control path is dead. It also means a DHCP
lease change repairs itself on the next scan instead of stranding the
entity at a stale address.

The probe is the device-info command, ``cmd:0``. It reads metadata and
cannot change device state, which matters for something broadcast to every
device on the network.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass

from . import protocol

DISCOVERY_PORT = 6095
BROADCAST_TARGETS = ("255.255.255.255",)
DEFAULT_TIMEOUT = 3.0

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDevice:
    """A device that answered a discovery probe."""

    device_id: str
    ip: str
    mac: str | None = None
    product_id: str | None = None
    software_version: str | None = None
    hardware_version: str | None = None


def _probe_payload() -> bytes:
    """The read-only device-info frame, unterminated (UDP needs no framing)."""

    frame = {
        "cmd": protocol.CMD_INFO,
        "pv": protocol.PROTOCOL_VERSION,
        "sn": protocol.new_sn(),
        "msg": {},
    }
    return json.dumps(frame, separators=(",", ":")).encode()


def _listen(
    targets: list[str] | tuple[str, ...],
    port: int,
    timeout: float,
):
    """Broadcast a probe and yield each distinct device as it answers.

    A generator so callers choose how long to care: enumerating the network
    drains the whole window, while looking for one known device can stop the
    moment it replies.
    """

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    seen: set[str] = set()
    try:
        payload = _probe_payload()
        for target in targets:
            try:
                sock.sendto(payload, (target, port))
            except OSError as err:
                # A host with several interfaces may refuse one broadcast
                # address while another works; keep going.
                _LOGGER.debug("Discovery probe to %s failed: %s", target, err)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            sock.settimeout(remaining)
            try:
                data, address = sock.recvfrom(2048)
            except TimeoutError:
                return
            except OSError as err:
                _LOGGER.debug("Discovery receive failed: %s", err)
                return

            device = _parse(data, sender_ip=address[0])
            # A device may answer several targets; report it once.
            if device is not None and device.device_id not in seen:
                seen.add(device.device_id)
                yield device
    finally:
        sock.close()


def discover(
    *,
    targets: list[str] | tuple[str, ...] = BROADCAST_TARGETS,
    port: int = DISCOVERY_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Broadcast a discovery probe and collect everything that answers.

    Always waits out the full window: there is no way to know how many
    devices are listening, so a short circuit would mean missing some.
    """

    return list(_listen(targets, port, timeout))


def find_device_ip(
    device_id: str,
    *,
    targets: list[str] | tuple[str, ...] = BROADCAST_TARGETS,
    port: int = DISCOVERY_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Return one device's current LAN address, or ``None`` if it is silent.

    Returns the moment the wanted device answers. A poll that has just lost
    the local path is waiting on this before it can retry, so the timeout is
    an upper bound rather than a fixed cost.
    """

    for device in _listen(targets, port, timeout):
        if device.device_id == device_id:
            return device.ip
    return None


def _parse(data: bytes, *, sender_ip: str) -> DiscoveredDevice | None:
    """Turn one discovery reply into a device, or ``None`` if unusable."""

    frame = protocol.decode(data)
    if frame is None:
        return None

    message = frame.get("msg")
    if not isinstance(message, dict):
        return None

    device_id = message.get("did")
    if not device_id:
        return None

    return DiscoveredDevice(
        device_id=str(device_id),
        # The body normally carries the device's own view of its address;
        # the packet's source address is the authoritative fallback.
        ip=str(message.get("ip") or sender_ip),
        mac=message.get("mac") or None,
        product_id=message.get("pid") or None,
        software_version=message.get("sv") or None,
        hardware_version=message.get("hv") or None,
    )
