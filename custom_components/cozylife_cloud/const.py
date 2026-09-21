"""Constants for the CozyLife cloud-fallback integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cozylife_cloud"
MANUFACTURER: Final = "CozyLife"

# Config entry keys.
CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_KEY: Final = "device_key"
CONF_DEVICE_NAME: Final = "device_name"
CONF_RELAY_HOST: Final = "relay_host"
CONF_RELAY_PORT: Final = "relay_port"
CONF_LOCAL_IP: Final = "local_ip"
CONF_MODEL: Final = "model"

# Options.
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOCAL_TIMEOUT: Final = "local_timeout"
CONF_CLOUD_TIMEOUT: Final = "cloud_timeout"

# The local listener leaks with uptime, and polling it harder brings the
# failure on sooner, so this defaults deliberately slow for a switch.
DEFAULT_SCAN_INTERVAL: Final = 120
DEFAULT_LOCAL_TIMEOUT: Final = 5.0
DEFAULT_CLOUD_TIMEOUT: Final = 10.0

MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 3600

#: Datapoint 1 is the relay itself on every CozyLife switch.
DPID_SWITCH: Final = 1

#: Exposed on the entity so it is visible which path is carrying traffic.
ATTR_CONNECTION_PATH: Final = "connection_path"
