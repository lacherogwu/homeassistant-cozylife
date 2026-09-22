"""Metering sensors for a CozyLife socket.

A metering socket measures voltage, current, power and cumulative energy
and reports them on datapoints the vendor documents nowhere. See const.py
for how the units were established.

Sensors are only created for datapoints the device actually reports, so a
plain non-metering switch does not sprout four entities reading unknown
forever.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CozyLifeConfigEntry
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_MODEL,
    DOMAIN,
    DPID_CURRENT,
    DPID_ENERGY,
    DPID_POWER,
    DPID_VOLTAGE,
    MANUFACTURER,
)
from .coordinator import CozyLifeCoordinator


@dataclass(frozen=True, kw_only=True)
class CozyLifeSensorDescription(SensorEntityDescription):
    """A sensor backed by one datapoint."""

    dpid: int
    #: Multiplier from the device's raw unit to Home Assistant's.
    scale: float = 1.0
    convert: Callable[[float], float] | None = None


SENSORS: tuple[CozyLifeSensorDescription, ...] = (
    CozyLifeSensorDescription(
        key="power",
        translation_key="power",
        dpid=DPID_POWER,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CozyLifeSensorDescription(
        key="voltage",
        translation_key="voltage",
        dpid=DPID_VOLTAGE,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CozyLifeSensorDescription(
        key="current",
        translation_key="current",
        dpid=DPID_CURRENT,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        # The device reports milliamps; the energy dashboard expects amps.
        scale=0.001,
        suggested_display_precision=3,
    ),
    CozyLifeSensorDescription(
        key="energy",
        translation_key="energy",
        dpid=DPID_ENERGY,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # TOTAL_INCREASING rather than TOTAL: the device's counter resets,
        # and this is the state class that copes with that.
        state_class=SensorStateClass.TOTAL_INCREASING,
        # The device counts whole watt-hours.
        scale=0.001,
        suggested_display_precision=3,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyLifeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a sensor for each metering datapoint this device reports."""

    coordinator = entry.runtime_data
    reported = coordinator.data or {}

    async_add_entities(
        CozyLifeSensor(coordinator, entry, description)
        for description in SENSORS
        if str(description.dpid) in reported
    )


class CozyLifeSensor(CoordinatorEntity[CozyLifeCoordinator], SensorEntity):
    """One metering datapoint."""

    _attr_has_entity_name = True
    entity_description: CozyLifeSensorDescription

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        entry: CozyLifeConfigEntry,
        description: CozyLifeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        device_id = entry.data[CONF_DEVICE_ID]

        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=entry.data.get(CONF_MODEL),
            name=entry.data.get(CONF_DEVICE_NAME),
        )

    @property
    def native_value(self) -> float | None:
        """The reading, scaled into Home Assistant's unit."""

        data: dict[str, Any] = self.coordinator.data or {}
        raw = data.get(str(self.entity_description.dpid))
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        # A genuine zero is a reading, not a missing value: with the relay
        # open the meter really does read zero, and discarding it would
        # leave a hole in the history.
        scaled = value * self.entity_description.scale
        # Whole-number readings stay whole, so a 63 W draw reads "63 W"
        # rather than "63.0 W".
        return int(scaled) if scaled.is_integer() else scaled
