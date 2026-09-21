"""The switch entity, end to end through a real Home Assistant instance.

These drive the config entry, the coordinator and the entity together,
since the thing worth protecting is the behaviour the user sees: the switch
reflecting device state, surviving the local listener dying, and saying
which path it is on.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.cozylife_cloud.api.errors import TransportError
from custom_components.cozylife_cloud.const import (
    ATTR_CONNECTION_PATH,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_DEVICE_NAME,
    CONF_LOCAL_IP,
    CONF_MODEL,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

ENTITY_ID = "switch.test_socket"

ENTRY_DATA = {
    CONF_DEVICE_ID: "aaaabbbbccccdddd0001",
    CONF_DEVICE_KEY: "test-key-not-a-real-one",
    CONF_DEVICE_NAME: "Test Socket",
    CONF_RELAY_HOST: "203.0.113.10",
    CONF_RELAY_PORT: 8898,
    CONF_LOCAL_IP: "192.0.2.50",
    CONF_MODEL: "Metering Socket",
}


class StubClient:
    """Stands in for the fallback client, which has its own tests."""

    def __init__(self, data=None, *, broken=False, accepts=True, path="local"):
        self.data = data if data is not None else {"1": 1}
        self.broken = broken
        self.accepts = accepts
        self.active_path = path
        self.has_cloud_fallback = True
        self.local_circuit_open = False
        self.controls = []
        self.closed = False

    def close(self):
        self.closed = True

    def query(self, attrs=None):
        if self.broken:
            raise TransportError("both paths failed")
        return self.data

    def control(self, payload):
        self.controls.append(payload)
        if self.broken:
            raise TransportError("both paths failed")
        return self.accepts


async def setup_integration(hass: HomeAssistant, client: StubClient) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Test Socket",
        unique_id=ENTRY_DATA[CONF_DEVICE_ID],
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.cozylife_cloud.build_client", return_value=client
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


async def poll_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run the refresh the scheduled poll would run.

    Home Assistant schedules coordinator refreshes on the event loop's
    monotonic clock, which a frozen wall clock does not move, so driving the
    refresh directly is the honest way to exercise what a poll does. That
    the poll is scheduled at all is asserted separately, by
    test_it_polls_at_the_configured_interval.
    """

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_the_switch_appears_and_reports_the_device_state(hass: HomeAssistant):
    await setup_integration(hass, StubClient({"1": 1}))

    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_datapoint_zero_reads_as_off(hass: HomeAssistant):
    await setup_integration(hass, StubClient({"1": 0}))

    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_turning_it_on_sets_the_switch_datapoint(hass: HomeAssistant):
    client = StubClient({"1": 0})
    await setup_integration(hass, client)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )

    assert client.controls == [{"1": 1}]


async def test_turning_it_off_sets_the_switch_datapoint(hass: HomeAssistant):
    client = StubClient({"1": 1})
    await setup_integration(hass, client)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )

    assert client.controls == [{"1": 0}]


async def test_the_state_updates_without_waiting_for_the_next_poll(
    hass: HomeAssistant,
):
    """Polling is deliberately slow to spare the device's listener, so a
    command has to reflect immediately or the UI feels broken."""

    client = StubClient({"1": 0})
    await setup_integration(hass, client)

    client.data = {"1": 1}
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_it_reports_which_path_is_serving_it(hass: HomeAssistant):
    """The whole point of the integration is that the path changes silently.
    Surfacing it makes that visible without reading the logs."""

    await setup_integration(hass, StubClient({"1": 1}, path="cloud"))

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_CONNECTION_PATH] == "cloud"


async def test_a_device_unreachable_at_startup_leaves_setup_to_retry(
    hass: HomeAssistant,
):
    """Home Assistant may well start before the network is ready. Retrying
    beats permanently failing the entry, and the entity is restored from the
    registry as unavailable in the meantime."""

    entry = await setup_integration(hass, StubClient(broken=True))

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_it_goes_unavailable_when_both_paths_fail_while_running(
    hass: HomeAssistant,
):
    """Losing the local listener alone must not do this -- that is what the
    cloud path is for. Only losing both is a genuine outage."""

    client = StubClient({"1": 1})
    entry = await setup_integration(hass, client)
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    client.broken = True
    await poll_again(hass, entry)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_losing_only_the_local_path_keeps_the_switch_working(
    hass: HomeAssistant,
):
    """The entire purpose of the integration, stated as a test."""

    client = StubClient({"1": 1}, path="local")
    entry = await setup_integration(hass, client)

    client.active_path = "cloud"
    await poll_again(hass, entry)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_CONNECTION_PATH] == "cloud"


async def test_it_is_registered_against_the_device_not_a_bare_entity(
    hass: HomeAssistant,
):
    from homeassistant.helpers import device_registry as dr

    await setup_integration(hass, StubClient({"1": 1}))

    devices = dr.async_get(hass)
    device = devices.async_get_device({(DOMAIN, ENTRY_DATA[CONF_DEVICE_ID])})
    assert device is not None
    assert device.manufacturer == "CozyLife"
    assert device.model == "Metering Socket"


async def test_the_entity_is_uniquely_identified_by_the_device_id(
    hass: HomeAssistant,
):
    from homeassistant.helpers import entity_registry as er

    await setup_integration(hass, StubClient({"1": 1}))

    entities = er.async_get(hass)
    entry = entities.async_get(ENTITY_ID)
    assert entry.unique_id == ENTRY_DATA[CONF_DEVICE_ID]


async def test_the_entry_unloads_cleanly(hass: HomeAssistant):
    """Home Assistant keeps the entity registered and shows it as restored
    and unavailable, so the check is that unloading succeeds and the entity
    stops reporting a state it can no longer vouch for."""

    entry = await setup_integration(hass, StubClient({"1": 1}))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_a_device_that_refuses_a_command_does_not_report_success(
    hass: HomeAssistant,
):
    """A refusal must not leave Home Assistant showing the state the user
    asked for; the next poll would silently contradict it."""

    client = StubClient({"1": 0}, accepts=False)
    await setup_integration(hass, client)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
        )

    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_it_polls_at_the_configured_interval(hass: HomeAssistant):
    """The default is deliberately slow: polling the local listener harder
    appears to bring on the firmware fault this works around."""

    entry = await setup_integration(hass, StubClient({"1": 1}))

    assert entry.runtime_data.update_interval == timedelta(
        seconds=DEFAULT_SCAN_INTERVAL
    )


async def test_the_poll_interval_option_is_honoured(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Test Socket",
        unique_id=ENTRY_DATA[CONF_DEVICE_ID],
        options={"scan_interval": 45},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.cozylife_cloud.build_client",
        return_value=StubClient({"1": 1}),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.runtime_data.update_interval == timedelta(seconds=45)


async def test_unloading_releases_the_devices_connection(hass: HomeAssistant):
    """The local transport holds a socket open between polls. A reload that
    left it dangling would leak one each time -- on a device whose fault is
    thought to be leaked connections."""

    client = StubClient({"1": 1})
    entry = await setup_integration(hass, client)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert client.closed is True


async def test_runtime_rediscovery_also_broadcasts_on_every_interface(
    hass: HomeAssistant,
):
    """The same routing problem applies to the rediscovery that repairs a
    DHCP lease change, not just to initial setup."""

    from custom_components.cozylife_cloud import build_client

    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Test Socket",
        unique_id=ENTRY_DATA[CONF_DEVICE_ID],
    )
    entry.add_to_hass(hass)

    seen = {}

    def record(device_id, *, targets=None, **kwargs):
        seen["targets"] = targets
        return None

    with patch(
        "custom_components.cozylife_cloud.find_device_ip", side_effect=record
    ):
        client = build_client(entry, ["255.255.255.255", "192.0.2.255"])
        client._local._rediscover("whatever")

    assert seen["targets"] == ["255.255.255.255", "192.0.2.255"]
