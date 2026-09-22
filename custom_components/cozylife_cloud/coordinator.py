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
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    METERING_DPIDS,
)

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
        #: None until the metering group has been probed; () if unsupported.
        self._metering: tuple[int, ...] | None = None
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
            return await self.hass.async_add_executor_job(self._poll)
        except TransportError as err:
            # Both paths are down. Entities go unavailable rather than
            # showing a state nobody can vouch for.
            raise UpdateFailed(str(err)) from err

    def _poll(self) -> dict[str, Any]:
        """One poll: the wildcard reply, plus metering if this device has it.

        Blocking; runs in an executor. The wildcard query is the reliable
        part and its failure fails the poll. The metering query is a bonus
        and its failure must not take the switch down with it.
        """

        # Copied, not used in place: merging the metering readings into
        # whatever the transport handed back would mutate an object this
        # method does not own.
        data = dict(self.client.query())

        if self._metering is None:
            self._metering = METERING_DPIDS if self._probe_metering(data) else ()
        elif self._metering:
            try:
                data.update(self.client.query(list(self._metering)))
            except TransportError as err:
                _LOGGER.debug("Metering query failed, keeping the rest: %s", err)
        return data

    def _probe_metering(self, data: dict[str, Any]) -> bool:
        """Ask for the metering group once, to find out whether it exists.

        Probed as a group and abandoned as a group: a device that does not
        report one of these answers the whole query with nothing, and
        repeatedly asking a device for datapoints it lacks is what rebooted
        the real hardware.
        """

        try:
            readings = self.client.query(list(METERING_DPIDS))
        except TransportError as err:
            _LOGGER.debug("Device reports no metering datapoints: %s", err)
            return False

        if not readings:
            return False

        data.update(readings)
        _LOGGER.debug("Device reports metering datapoints %s", sorted(readings))
        return True

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
