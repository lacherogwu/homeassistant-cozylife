"""Local-first, cloud-fallback orchestration.

The device's local TCP listener leaks and stops answering after a few days
of uptime -- a firmware defect with no client-side fix, recoverable only by
power-cycling it. Home Assistant's stock behaviour in that situation is to
mark the entity unavailable until someone notices. CozyLife's cloud relay
keeps working the whole time, which is why the phone app never notices.

This class sits between the entity and the two transports: it prefers the
local one, falls back to the relay when local is unreachable, and returns
to local as soon as it recovers.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .errors import TransportError

_LOGGER = logging.getLogger(__name__)


class Transport(Protocol):
    """What both transports expose. Nothing else is required of them."""

    name: str

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]: ...

    def control(self, payload: dict[Any, int]) -> bool: ...


class FallbackClient:
    """Serve each request over the local transport, or the cloud if it must.

    ``cloud`` may be ``None``, which makes this local-only -- the state the
    integration is in before cloud credentials have been supplied.
    """

    def __init__(self, local: Transport, cloud: Transport | None) -> None:
        self._local = local
        self._cloud = cloud
        self._active_path: str | None = None

    @property
    def active_path(self) -> str | None:
        """Which transport served the most recent request, for diagnostics."""

        return self._active_path

    @property
    def has_cloud_fallback(self) -> bool:
        return self._cloud is not None

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]:
        """Read datapoints over whichever path is working."""

        return self._attempt(lambda transport: transport.query(attrs))

    def control(self, payload: dict[Any, int]) -> bool:
        """Write datapoints over whichever path is working.

        A device that answers and refuses the command returns ``False`` and
        is *not* retried on the other path: it was reached, the cloud cannot
        do better, and a retry would drive the hardware a second time.
        """

        return self._attempt(lambda transport: transport.control(payload))

    def _attempt(self, call):
        try:
            result = call(self._local)
        except TransportError as local_error:
            if self._cloud is None:
                self._active_path = None
                raise

            _LOGGER.debug("Local transport failed, trying cloud: %s", local_error)
            try:
                result = call(self._cloud)
            except TransportError as cloud_error:
                self._active_path = None
                raise TransportError(
                    f"both paths failed -- local: {local_error}; cloud: {cloud_error}"
                ) from cloud_error

            self._note_path(self._cloud.name)
            return result

        self._note_path(self._local.name)
        return result

    def _note_path(self, path: str) -> None:
        """Record the serving path, announcing only genuine transitions.

        Polling runs all day; logging every request at INFO would bury the
        one event that matters. A transition is exactly the thing worth
        seeing in ``ha core logs``.
        """

        if path == self._active_path:
            _LOGGER.debug("Served over the %s path", path)
            return

        if self._active_path is None:
            _LOGGER.info("CozyLife requests are being served over the %s path", path)
        else:
            _LOGGER.info(
                "CozyLife switched from the %s path to the %s path",
                self._active_path,
                path,
            )
        self._active_path = path
