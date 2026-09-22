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

# Metering datapoints, present on metering sockets and absent on plain ones.
# The vendor documents none of these; the numbers were found by watching the
# CozyLife app's own traffic, and the units were then measured rather than
# assumed:
#
#   Voltage anchors everything -- 245 can only be volts. Current at 515 mA
#   then gives 126 VA apparent, so power at 64.7 must be watts (power factor
#   0.51, typical of a non-PFC LED driver) and not tenths of a watt, which
#   would imply an impossible 0.05.
#
#   Energy was measured directly: over 361 s at a mean 64.7 W the counter
#   moved 6, against 6.50 predicted for watt-hours and 0.65 for tenths. So
#   it counts whole watt-hours.
#
# The energy counter resets -- it was observed dropping from 560 to 0 -- so
# the sensor uses TOTAL_INCREASING, which expects that.
DPID_ENERGY: Final = 26
DPID_CURRENT: Final = 27
DPID_POWER: Final = 28
DPID_VOLTAGE: Final = 29

#: Fetched as one explicit query, because the device's wildcard reply leaves
#: current and voltage out. Asked for as a group and abandoned as a group:
#: requesting a datapoint the device does not report returns nothing at all
#: rather than the subset it does know, and a long list of unknown ones was
#: observed to reboot the device outright -- resetting its energy counter and
#: dropping its relay. So this is probed exactly once per setup.
METERING_DPIDS: Final = (DPID_ENERGY, DPID_CURRENT, DPID_POWER, DPID_VOLTAGE)

#: Exposed on the entity so it is visible which path is carrying traffic.
ATTR_CONNECTION_PATH: Final = "connection_path"
