"""Config and options flows.

Setting up a device needs two things the user cannot reasonably be asked
for: the device's ``device_key``, which authenticates it to CozyLife's
relay, and the relay endpoint it is registered on. Both come from the
account's own device list, so the flow logs in once to read it.

The account password is used for that single call and then dropped. Only
the per-device key is persisted, which is the narrowest credential that
still makes the fallback path work.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.components.network import async_get_ipv4_broadcast_addresses
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .api.account import CozyLifeAccount, CozyLifeDevice
from .api.discovery import find_device_ip
from .api.errors import AuthError, CozyLifeError
from .const import (
    CONF_CLOUD_TIMEOUT,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_DEVICE_NAME,
    CONF_LOCAL_IP,
    CONF_LOCAL_TIMEOUT,
    CONF_MODEL,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_CLOUD_TIMEOUT,
    DEFAULT_LOCAL_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


async def async_broadcast_targets(hass) -> list[str]:
    """Every IPv4 broadcast address on this host, for a discovery probe."""

    return [str(address) for address in await async_get_ipv4_broadcast_addresses(hass)]


def _fetch_devices(email: str, password: str) -> list[CozyLifeDevice]:
    """Log in and read the account's device list. Blocking; runs in executor."""

    account = CozyLifeAccount()
    token = account.login(email, password)
    return account.devices(token)


class CozyLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user from account credentials to one configured device."""

    VERSION = 1

    def __init__(self) -> None:
        self._devices: list[CozyLifeDevice] = []
        self._device: CozyLifeDevice | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the CozyLife account and read its device list."""

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                devices = await self.hass.async_add_executor_job(
                    _fetch_devices, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except CozyLifeError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    return self.async_abort(reason="no_devices")

                self._devices = devices
                if len(devices) == 1:
                    return await self._async_configure(devices[0])
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user", data_schema=CREDENTIALS_SCHEMA, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which of the account's devices to add."""

        if user_input is not None:
            chosen = next(
                device
                for device in self._devices
                if device.device_id == user_input[CONF_DEVICE_ID]
            )
            return await self._async_configure(chosen)

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): vol.In(
                        {device.device_id: device.name for device in self._devices}
                    )
                }
            ),
        )

    async def async_step_address(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the device's LAN address when discovery cannot find it."""

        assert self._device is not None

        if user_input is not None:
            return self._create_entry(self._device, user_input[CONF_LOCAL_IP])

        return self.async_show_form(
            step_id="address",
            data_schema=vol.Schema({vol.Required(CONF_LOCAL_IP): cv.string}),
            description_placeholders={"device": self._device.name},
        )

    async def _async_configure(self, device: CozyLifeDevice) -> ConfigFlowResult:
        """Locate the chosen device on the LAN and finish, or ask where it is."""

        await self.async_set_unique_id(device.device_id)
        self._abort_if_unique_id_configured()

        self._device = device
        # The account knows the relay address but never the device's own, so
        # the local path needs discovery to supply it.
        #
        # Broadcast on every interface Home Assistant knows about, not just
        # 255.255.255.255: a host with a LAN adapter and Docker bridges can
        # send that out the wrong one, and the probe never reaches the LAN.
        targets = await async_broadcast_targets(self.hass)
        local_ip = await self.hass.async_add_executor_job(
            partial(find_device_ip, device.device_id, targets=targets)
        )
        if not local_ip:
            return await self.async_step_address()

        return self._create_entry(device, local_ip)

    def _create_entry(
        self, device: CozyLifeDevice, local_ip: str
    ) -> ConfigFlowResult:
        """Store everything the integration needs -- and nothing more."""

        return self.async_create_entry(
            title=device.name,
            data={
                CONF_DEVICE_ID: device.device_id,
                CONF_DEVICE_KEY: device.device_key,
                CONF_DEVICE_NAME: device.name,
                CONF_RELAY_HOST: device.relay_host,
                CONF_RELAY_PORT: device.relay_port,
                CONF_LOCAL_IP: local_ip,
                CONF_MODEL: device.model,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CozyLifeOptionsFlow()


class CozyLifeOptionsFlow(OptionsFlow):
    """Tuning that should not require re-adding the device."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Polling the local listener harder appears to bring its
                    # failure on sooner, so this is deliberately adjustable.
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Optional(
                        CONF_LOCAL_TIMEOUT,
                        default=options.get(
                            CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30)),
                    vol.Optional(
                        CONF_CLOUD_TIMEOUT,
                        default=options.get(
                            CONF_CLOUD_TIMEOUT, DEFAULT_CLOUD_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=60)),
                }
            ),
        )
