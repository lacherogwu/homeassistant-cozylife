"""Metering sensors.

The socket measures voltage, current, power and cumulative energy, and
reports them on datapoints the vendor never documents. Nothing in Home
Assistant could see any of it before this.

Units were established empirically rather than guessed -- see the comments
in const.py -- because publishing a sensor with the wrong scale is worse
than publishing no sensor at all.
"""


from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .test_switch import ENTRY_DATA, StubClient, setup_integration

POWER = "sensor.test_socket_power"
VOLTAGE = "sensor.test_socket_voltage"
CURRENT = "sensor.test_socket_current"
ENERGY = "sensor.test_socket_energy"

# A realistic reading: relay closed, ~63 W at 247 V drawing 499 mA.
LIVE = {"1": 1, "26": 560, "27": 499, "28": 63, "29": 247}


async def test_it_exposes_power(hass: HomeAssistant):
    await setup_integration(hass, StubClient(LIVE))

    state = hass.states.get(POWER)
    assert state.state == "63"
    assert state.attributes["device_class"] == SensorDeviceClass.POWER
    assert state.attributes["unit_of_measurement"] == "W"
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT


async def test_it_exposes_voltage(hass: HomeAssistant):
    await setup_integration(hass, StubClient(LIVE))

    state = hass.states.get(VOLTAGE)
    assert state.state == "247"
    assert state.attributes["device_class"] == SensorDeviceClass.VOLTAGE
    assert state.attributes["unit_of_measurement"] == "V"


async def test_current_is_converted_from_milliamps(hass: HomeAssistant):
    """The device reports mA; Home Assistant's energy UI expects amps."""

    await setup_integration(hass, StubClient(LIVE))

    state = hass.states.get(CURRENT)
    assert float(state.state) == 0.499
    assert state.attributes["device_class"] == SensorDeviceClass.CURRENT
    assert state.attributes["unit_of_measurement"] == "A"


async def test_energy_is_total_increasing_so_it_reaches_the_energy_dashboard(
    hass: HomeAssistant,
):
    """TOTAL_INCREASING is what lets Home Assistant treat this as a meter
    and cope with the counter resetting when the device reboots."""

    await setup_integration(hass, StubClient(LIVE))

    state = hass.states.get(ENERGY)
    assert float(state.state) == 0.560
    assert state.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_the_sensors_belong_to_the_same_device_as_the_switch(
    hass: HomeAssistant,
):
    from homeassistant.helpers import entity_registry as er

    await setup_integration(hass, StubClient(LIVE))
    entities = er.async_get(hass)

    for entity_id in (POWER, VOLTAGE, CURRENT, ENERGY):
        entry = entities.async_get(entity_id)
        assert entry is not None, f"{entity_id} missing"
        assert entry.unique_id.startswith(ENTRY_DATA["device_id"])


async def test_readings_track_the_device(hass: HomeAssistant):
    client = StubClient(LIVE)
    entry = await setup_integration(hass, client)

    client.data = {**LIVE, "28": 41, "29": 233}
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(POWER).state == "41"
    assert hass.states.get(VOLTAGE).state == "233"


async def test_a_datapoint_the_device_omits_does_not_create_a_sensor(
    hass: HomeAssistant,
):
    """Not every CozyLife switch meters. A plain socket should not sprout
    four sensors that permanently read unknown."""

    await setup_integration(hass, StubClient({"1": 1}))

    assert hass.states.get(POWER) is None
    assert hass.states.get(ENERGY) is None


async def test_sensors_go_unavailable_when_both_paths_fail(hass: HomeAssistant):
    client = StubClient(LIVE)
    entry = await setup_integration(hass, client)

    client.broken = True
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(POWER).state == STATE_UNAVAILABLE


async def test_a_zero_reading_is_reported_not_discarded(hass: HomeAssistant):
    """With the relay open the meter genuinely reads zero. Treating that as
    'no value' would leave a gap in the energy history."""

    await setup_integration(hass, StubClient({**LIVE, "1": 0, "27": 0, "28": 0}))

    assert hass.states.get(POWER).state == "0"
    assert float(hass.states.get(CURRENT).state) == 0.0


# --- how the metering datapoints are fetched --------------------------------
#
# The device's wildcard query (attr [0]) does NOT return every datapoint: it
# omits current and voltage. They only come back when asked for by name.
#
# But an explicit query is dangerous. Asking for a datapoint the device does
# not report poisons the whole query -- it returns nothing rather than the
# subset it does know -- and a long list of unknown ones was observed to
# reboot the device outright, resetting its energy counter and dropping its
# relay. So the metering set is probed once, as a group, and dropped
# entirely if the device does not answer.


class MeteringClient(StubClient):
    """A client that distinguishes the wildcard query from an explicit one."""

    def __init__(self, wildcard, explicit=None, **kw):
        super().__init__(wildcard, **kw)
        self.explicit = explicit
        self.queries: list[object] = []

    def query(self, attrs=None):
        self.queries.append(tuple(attrs) if attrs else None)
        if self.broken:
            from custom_components.cozylife_cloud.api.errors import TransportError
            raise TransportError("both paths failed")
        if attrs:
            if self.explicit is None:
                from custom_components.cozylife_cloud.api.errors import TransportError
                raise TransportError("device did not answer that attribute list")
            return self.explicit
        return self.data


BASE = {"1": 1, "26": 560, "28": 63}          # what the wildcard returns
EXTRA = {"26": 560, "27": 499, "28": 63, "29": 247}   # the metering group


async def test_voltage_and_current_are_fetched_explicitly(hass: HomeAssistant):
    """They are absent from the wildcard reply, so without this there are no
    voltage or current sensors at all."""

    client = MeteringClient(BASE, EXTRA)
    await setup_integration(hass, client)

    assert hass.states.get(VOLTAGE).state == "247"
    assert float(hass.states.get(CURRENT).state) == 0.499


async def test_the_metering_query_is_always_the_same_attribute_list(
    hass: HomeAssistant,
):
    """A metering device is asked every poll, so the readings stay fresh.
    What must never vary is *which* datapoints are asked for: the device
    answers an unfamiliar list with nothing at all."""

    client = MeteringClient(BASE, EXTRA)
    entry = await setup_integration(hass, client)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    explicit = [q for q in client.queries if q is not None]
    assert len(explicit) == 4, "one probe at setup, then one per poll"
    assert len(set(explicit)) == 1, f"the list varied between polls: {set(explicit)}"


async def test_a_device_without_metering_is_not_asked_again(
    hass: HomeAssistant,
):
    """Asking repeatedly for datapoints it does not have is exactly what
    rebooted the real device."""

    client = MeteringClient(BASE, explicit=None)
    entry = await setup_integration(hass, client)
    for _ in range(4):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    explicit = [q for q in client.queries if q is not None]
    assert len(explicit) == 1, f"probed {len(explicit)} times, should be once"


async def test_a_device_without_metering_still_works(hass: HomeAssistant):
    client = MeteringClient(BASE, explicit=None)
    await setup_integration(hass, client)

    assert hass.states.get(POWER) is not None      # 28 is in the wildcard
    assert hass.states.get(VOLTAGE) is None        # 29 is not, and was refused
    assert hass.states.get("switch.test_socket").state == "on"


async def test_a_failed_metering_query_does_not_fail_the_poll(
    hass: HomeAssistant,
):
    """Metering is a bonus. Losing it must not take the switch down."""

    client = MeteringClient(BASE, explicit=None)
    entry = await setup_integration(hass, client)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data.last_update_success is True
    assert hass.states.get("switch.test_socket").state == "on"
