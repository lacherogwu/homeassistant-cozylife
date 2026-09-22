"""CozyLife devices with a cloud fallback path.

CozyLife's local TCP protocol is fast and private, but on at least some of
their hardware the device's listener leaks and stops accepting connections
after a few days of uptime -- a firmware fault with no client-side fix.
Home Assistant's usual response is to mark the entity unavailable until
someone power-cycles the socket.

This integration keeps the local path as the default and falls back to
CozyLife's own cloud relay, the same one the phone app uses, when the local
listener stops answering. The relay keeps working throughout, so the entity
stays usable and repairs itself when the listener comes back.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api.cloud import CloudRelayTransport
from .api.discovery import find_device_ip
from .api.fallback import FallbackClient
from .api.rediscovering import RediscoveringLocalTransport
from .config_flow import async_broadcast_targets
from .const import (
    CONF_CLOUD_TIMEOUT,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_LOCAL_IP,
    CONF_LOCAL_TIMEOUT,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    DEFAULT_CLOUD_TIMEOUT,
    DEFAULT_LOCAL_TIMEOUT,
)
from .coordinator import CozyLifeCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

type CozyLifeConfigEntry = ConfigEntry[CozyLifeCoordinator]

_LOGGER = logging.getLogger(__name__)


def build_client(
    entry: ConfigEntry, broadcast_targets: list[str]
) -> FallbackClient:
    """Assemble the two transports for a configured device.

    Synchronous and side-effect free -- it opens no sockets -- so it is safe
    to call directly from the event loop.

    ``broadcast_targets`` is where a rediscovery probe is sent. It comes
    from Home Assistant rather than being hardcoded to 255.255.255.255,
    because a host with a LAN adapter and Docker bridges can route that out
    the wrong interface, leaving the probe never reaching the device.
    """

    data = entry.data
    options = entry.options
    device_id = data[CONF_DEVICE_ID]

    local = RediscoveringLocalTransport(
        device_id,
        data[CONF_LOCAL_IP],
        timeout=options.get(CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT),
        rediscover=lambda did: find_device_ip(did, targets=broadcast_targets),
    )

    # The cloud half is optional: without a relay endpoint the integration
    # still works, it just has nothing to fall back to.
    relay_host = data.get(CONF_RELAY_HOST)
    cloud = None
    if relay_host and data.get(CONF_DEVICE_KEY):
        cloud = CloudRelayTransport(
            device_id,
            data[CONF_DEVICE_KEY],
            relay_host,
            int(data[CONF_RELAY_PORT]),
            timeout=options.get(CONF_CLOUD_TIMEOUT, DEFAULT_CLOUD_TIMEOUT),
        )

    return FallbackClient(local, cloud)


async def async_setup_entry(hass: HomeAssistant, entry: CozyLifeConfigEntry) -> bool:
    """Set up a CozyLife device from a config entry."""

    targets = await async_broadcast_targets(hass)
    coordinator = CozyLifeCoordinator(hass, entry, build_client(entry, targets))
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CozyLifeConfigEntry) -> bool:
    """Tear down a config entry, releasing the connection it holds."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # The local transport keeps a socket open between polls; a reload
        # that left it dangling would leak one every time.
        await hass.async_add_executor_job(entry.runtime_data.client.close)
    return unloaded


async def _async_reload_on_update(
    hass: HomeAssistant, entry: CozyLifeConfigEntry
) -> None:
    """Rebuild the client when options change, so new timeouts take effect."""

    await hass.config_entries.async_reload(entry.entry_id)
