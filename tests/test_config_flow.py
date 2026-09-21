"""Setting the integration up from the UI.

The flow logs in to CozyLife once to read the account's device list, which
is the only source of a device's ``device_key`` and its assigned relay
endpoint. The account password is used for that one call and then
discarded -- it is never written to the config entry.
"""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cozylife_cloud.api.account import CozyLifeDevice
from custom_components.cozylife_cloud.api.errors import AuthError, CozyLifeError
from custom_components.cozylife_cloud.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_LOCAL_IP,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

CREDENTIALS = {CONF_EMAIL: "someone@example.com", CONF_PASSWORD: "hunter2"}

SOCKET = CozyLifeDevice(
    device_id="aaaabbbbccccdddd0001",
    device_key="fake-key-one",
    name="Test Socket",
    relay_host="203.0.113.10",
    relay_port=8898,
    model="Metering Socket",
)
LAMP = CozyLifeDevice(
    device_id="aaaabbbbccccdddd0002",
    device_key="fake-key-two",
    name="Test Lamp",
    relay_host="203.0.113.11",
    relay_port=8898,
)


def cloud(devices=(SOCKET,), *, login_error=None):
    """Patch the account client the flow uses."""

    account = patch("custom_components.cozylife_cloud.config_flow.CozyLifeAccount")
    instance = account.start()
    if login_error is not None:
        instance.return_value.login.side_effect = login_error
    else:
        instance.return_value.login.return_value = "fake-token"
    instance.return_value.devices.return_value = list(devices)
    return account


def found_at(ip="192.0.2.50"):
    return patch(
        "custom_components.cozylife_cloud.config_flow.find_device_ip",
        return_value=ip,
    )


async def start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_it_opens_by_asking_for_the_account(hass: HomeAssistant):
    result = await start(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_a_single_device_account_is_configured_in_one_step(
    hass: HomeAssistant,
):
    account = cloud()
    try:
        with found_at(), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Socket"
    assert result["data"][CONF_DEVICE_ID] == SOCKET.device_id
    assert result["data"][CONF_DEVICE_KEY] == SOCKET.device_key
    assert result["data"][CONF_RELAY_HOST] == SOCKET.relay_host
    assert result["data"][CONF_RELAY_PORT] == SOCKET.relay_port
    assert result["data"][CONF_LOCAL_IP] == "192.0.2.50"


async def test_the_account_password_is_never_written_to_the_entry(
    hass: HomeAssistant,
):
    """It is needed for exactly one call. Persisting it would put a reusable
    account credential in .storage for no benefit."""

    account = cloud()
    try:
        with found_at(), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert "hunter2" not in str(result["data"])
    assert CONF_PASSWORD not in result["data"]


async def test_a_rejected_password_is_reported_on_the_form(hass: HomeAssistant):
    account = cloud(login_error=AuthError("CozyLife rejected the password"))
    try:
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_an_unreachable_api_is_reported_on_the_form(hass: HomeAssistant):
    account = cloud(login_error=CozyLifeError("cannot reach CozyLife"))
    try:
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_account_with_no_devices_says_so(hass: HomeAssistant):
    account = cloud(devices=())
    try:
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices"


async def test_several_devices_are_offered_as_a_choice(hass: HomeAssistant):
    account = cloud(devices=(SOCKET, LAMP))
    try:
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device"


async def test_choosing_one_of_several_devices_configures_it(hass: HomeAssistant):
    account = cloud(devices=(SOCKET, LAMP))
    try:
        with found_at(), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_DEVICE_ID: LAMP.device_id}
            )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == LAMP.device_id


async def test_a_device_that_is_already_configured_is_not_added_twice(
    hass: HomeAssistant,
):
    MockConfigEntry(
        domain=DOMAIN, unique_id=SOCKET.device_id, data={}
    ).add_to_hass(hass)

    account = cloud()
    try:
        with found_at():
            result = await start(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_device_that_discovery_cannot_find_is_asked_for_by_address(
    hass: HomeAssistant,
):
    """The account knows the relay address but never the LAN one. If the
    device is not answering discovery -- switched off, or on another
    subnet -- the user can still supply it."""

    account = cloud()
    try:
        with patch(
            "custom_components.cozylife_cloud.config_flow.find_device_ip",
            return_value=None,
        ), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
            assert result["step_id"] == "address"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOCAL_IP: "192.0.2.77"}
            )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOCAL_IP] == "192.0.2.77"


async def test_the_poll_interval_can_be_changed_afterwards(hass: HomeAssistant):
    """Polling the local listener harder appears to bring its failure on
    sooner, so this needs to be tunable without reconfiguring the device."""

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SOCKET.device_id, data={}, options={}
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.cozylife_cloud.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 300}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL] == 300


async def test_discovery_broadcasts_on_every_interface(hass: HomeAssistant):
    """Home Assistant runs on a host with several interfaces -- a LAN
    adapter plus Docker bridges. A probe sent only to 255.255.255.255 can
    leave on the wrong one and never reach the device, which is exactly
    what happened on the real instance. Asking Home Assistant for every
    interface's broadcast address makes the probe reach the LAN."""

    account = cloud()
    seen = {}

    def record(device_id, *, targets=None, **kwargs):
        seen["targets"] = targets
        return "192.0.2.50"

    try:
        with patch(
            "custom_components.cozylife_cloud.config_flow.find_device_ip",
            side_effect=record,
        ), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start(hass)
            await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert seen["targets"], "discovery was given no broadcast targets"
    assert "255.255.255.255" in seen["targets"]


# --- reconfigure -----------------------------------------------------------
#
# A device's control key changes if it is re-paired in the CozyLife app, and
# its relay endpoint can be reassigned. Without this, the only recovery is
# deleting the entry and adding it again -- losing the entity id, and with it
# every dashboard and automation referring to it.

RECONFIGURE_DATA = {
    CONF_DEVICE_ID: SOCKET.device_id,
    CONF_DEVICE_KEY: "stale-key",
    CONF_RELAY_HOST: "203.0.113.99",
    CONF_RELAY_PORT: 8899,
    CONF_LOCAL_IP: "192.0.2.9",
    "device_name": SOCKET.name,
}


def existing_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=RECONFIGURE_DATA,
        title=SOCKET.name,
        unique_id=SOCKET.device_id,
    )
    entry.add_to_hass(hass)
    return entry


async def start_reconfigure(hass, entry):
    return await entry.start_reconfigure_flow(hass)


async def test_reconfigure_asks_for_the_account_again(hass: HomeAssistant):
    entry = existing_entry(hass)

    result = await start_reconfigure(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_refreshes_the_key_and_relay(hass: HomeAssistant):
    entry = existing_entry(hass)
    account = cloud()
    try:
        with found_at("192.0.2.77"), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start_reconfigure(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE_KEY] == SOCKET.device_key
    assert entry.data[CONF_RELAY_HOST] == SOCKET.relay_host
    assert entry.data[CONF_RELAY_PORT] == SOCKET.relay_port
    assert entry.data[CONF_LOCAL_IP] == "192.0.2.77"


async def test_reconfigure_keeps_the_same_entry(hass: HomeAssistant):
    """The whole point: the entity id, and everything referring to it,
    survives."""

    entry = existing_entry(hass)
    account = cloud()
    try:
        with found_at(), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start_reconfigure(hass, entry)
            await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_reports_a_rejected_password(hass: HomeAssistant):
    entry = existing_entry(hass)
    account = cloud(login_error=AuthError("rejected"))
    try:
        result = await start_reconfigure(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_DEVICE_KEY] == "stale-key"  # left untouched


async def test_reconfigure_says_so_when_the_device_left_the_account(
    hass: HomeAssistant,
):
    """Signing in with a different account, or after removing the device,
    should say what is wrong rather than silently rewriting the entry to
    point at someone else's socket."""

    entry = existing_entry(hass)
    account = cloud(devices=(LAMP,))
    try:
        result = await start_reconfigure(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    finally:
        account.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_not_found"


async def test_reconfigure_keeps_the_old_address_if_discovery_fails(
    hass: HomeAssistant,
):
    """A device that is merely switched off should not lose its known
    address as a side effect of refreshing its key."""

    entry = existing_entry(hass)
    account = cloud()
    try:
        with patch(
            "custom_components.cozylife_cloud.config_flow.find_device_ip",
            return_value=None,
        ), patch(
            "custom_components.cozylife_cloud.async_setup_entry", return_value=True
        ):
            result = await start_reconfigure(hass, entry)
            await hass.config_entries.flow.async_configure(
                result["flow_id"], CREDENTIALS
            )
    finally:
        account.stop()

    assert entry.data[CONF_LOCAL_IP] == "192.0.2.9"
