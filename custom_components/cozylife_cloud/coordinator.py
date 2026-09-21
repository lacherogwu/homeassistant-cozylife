"""Polling and command dispatch for one CozyLife device."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.errors import TransportError
from .api.fallback import FallbackClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps one device's datapoints current, over whichever path works."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FallbackClient,
    ) -> None:
        self.client = client
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )

    @property
    def connection_path(self) -> str | None:
        """Which transport served the most recent request."""

        return self.client.active_path

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll the device, over local if it answers and cloud if it does not."""

        try:
            return await self.hass.async_add_executor_job(self.client.query)
        except TransportError as err:
            # Both paths are down. Entities go unavailable rather than
            # showing a state nobody can vouch for.
            raise UpdateFailed(str(err)) from err

    async def async_control(self, payload: dict[str, int]) -> None:
        """Send a write command. Drives real hardware -- see WRITE_SAFETY.md.

        Applies the new value optimistically once the device confirms it,
        because polling is deliberately slow and waiting a full interval for
        the UI to catch up feels broken.
        """

        try:
            accepted = await self.hass.async_add_executor_job(
                self.client.control, payload
            )
        except TransportError as err:
            raise HomeAssistantError(
                f"Could not reach {self.name} on either path: {err}"
            ) from err

        if not accepted:
            # Surfaced rather than swallowed: leaving Home Assistant showing
            # the requested state would be contradicted by the next poll.
            raise HomeAssistantError(f"{self.name} refused the command")

        confirmed = dict(self.data or {})
        confirmed.update({str(dpid): value for dpid, value in payload.items()})
        self.async_set_updated_data(confirmed)

        # Reconcile against the device shortly after, in case it settled on
        # something other than what was asked for.
        await self.async_request_refresh()
