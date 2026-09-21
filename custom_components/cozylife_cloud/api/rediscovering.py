"""A local transport that keeps up with a device that changes address.

CozyLife devices take a DHCP lease like anything else. When one moves, a
transport pinned to the old address fails forever -- and because the cloud
fallback keeps the entity working, nothing looks broken. Every request
quietly takes the slow vendor path instead, which is a worse outcome than
an outage because nobody goes looking for it.

This wrapper closes that hole: when the local path fails, it asks UDP
discovery where the device is now, and retries only if the answer differs
from the address it was using.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from . import discovery
from .errors import TransportError
from .local import DEFAULT_TIMEOUT, LocalTransport

#: The listener stays dead for days at a time. Broadcasting on every poll
#: for that whole period would be rude to the rest of the network.
DEFAULT_REDISCOVERY_INTERVAL = 300.0

_LOGGER = logging.getLogger(__name__)


class RediscoveringLocalTransport:
    """Local transport for a device whose address may change underneath it."""

    def __init__(
        self,
        device_id: str,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport_factory: Callable[[str], Any] | None = None,
        rediscover: Callable[[str], str | None] | None = None,
        min_rediscovery_interval: float = DEFAULT_REDISCOVERY_INTERVAL,
    ) -> None:
        self._device_id = device_id
        self._host = host
        self._min_interval = min_rediscovery_interval
        self._last_search: float | None = None

        self._factory = transport_factory or (
            lambda address: LocalTransport(address, timeout=timeout)
        )
        self._rediscover = rediscover or (
            lambda device_id: discovery.find_device_ip(device_id)
        )
        self._transport = self._factory(host)

    @property
    def name(self) -> str:
        return "local"

    @property
    def host(self) -> str:
        """The address currently believed to be the device's."""

        return self._host

    def __repr__(self) -> str:
        return f"RediscoveringLocalTransport({self._device_id} at {self._host})"

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]:
        """Read datapoints, relocating the device first if it has moved."""

        try:
            return self._transport.query(attrs)
        except TransportError:
            if not self._relocate():
                raise
            _LOGGER.info(
                "CozyLife device %s moved to %s; retrying locally",
                self._device_id,
                self._host,
            )
            return self._transport.query(attrs)

    def control(self, payload: dict[Any, int]) -> bool:
        """Write datapoints. Drives real hardware -- see WRITE_SAFETY.md.

        A failed write is never retried here. It may have reached the device
        even though the reply did not, and re-sending it would risk driving
        the hardware a second time; the caller has a cloud path for that.
        The address is still refreshed, so the next poll benefits.
        """

        try:
            return self._transport.control(payload)
        except TransportError:
            self._relocate()
            raise

    def _relocate(self) -> bool:
        """Look for the device. Returns whether it turned up somewhere new.

        An unchanged address means the device is present but its TCP
        listener has stopped -- the firmware fault this integration works
        around. Retrying the same address cannot help, so that reports
        ``False`` and lets the caller fail over to the cloud.
        """

        now = time.monotonic()
        if self._last_search is not None and now - self._last_search < self._min_interval:
            return False
        self._last_search = now

        try:
            found = self._rediscover(self._device_id)
        except OSError as err:
            _LOGGER.debug("Discovery failed while relocating %s: %s", self._device_id, err)
            return False

        if not found or found == self._host:
            return False

        _LOGGER.debug(
            "CozyLife device %s: address changed %s -> %s",
            self._device_id,
            self._host,
            found,
        )
        self._host = found
        self._transport = self._factory(found)
        return True
