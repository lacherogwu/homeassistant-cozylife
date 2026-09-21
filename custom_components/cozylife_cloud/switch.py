"""The switch entity for a CozyLife socket."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CozyLifeConfigEntry
from .const import (
    ATTR_CONNECTION_PATH,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_MODEL,
    DOMAIN,
    DPID_SWITCH,
    MANUFACTURER,
)
from .coordinator import CozyLifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyLifeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch for a configured device."""

    async_add_entities([CozyLifeSwitch(entry.runtime_data, entry)])


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    """A CozyLife socket's relay, on datapoint 1."""

    _attr_has_entity_name = True
    _attr_name = None  # the device's name is the entity's name

    def __init__(
        self, coordinator: CozyLifeCoordinator, entry: CozyLifeConfigEntry
    ) -> None:
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]

        self._attr_unique_id = device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=entry.data.get(CONF_MODEL),
            name=entry.data.get(CONF_DEVICE_NAME),
        )

    @property
    def is_on(self) -> bool:
        """Whether the relay is closed."""

        data = self.coordinator.data or {}
        return int(data.get(str(DPID_SWITCH), 0)) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose which path is carrying traffic.

        Failover is meant to be invisible, which makes it hard to notice
        that local control has quietly stopped working. This surfaces it
        without having to read the logs.
        """

        return {ATTR_CONNECTION_PATH: self.coordinator.connection_path}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close the relay. Drives real hardware -- see WRITE_SAFETY.md."""

        await self.coordinator.async_control({str(DPID_SWITCH): 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open the relay. Drives real hardware -- see WRITE_SAFETY.md."""

        await self.coordinator.async_control({str(DPID_SWITCH): 0})
