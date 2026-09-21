# homeassistant-cozylife-cloud

**Status: private development. Not yet published. Do not push this repo to
GitHub until explicitly told to — see `WORKING_NOTES.md`.**

A Home Assistant custom integration for CozyLife devices that keeps
working when the device's local control path dies.

## The problem

CozyLife devices expose a local TCP control protocol on port 5555. On at
least some of their hardware that listener leaks and stops accepting
connections after a few days of uptime, recoverable only by physically
power-cycling the device. It is a firmware fault with no client-side fix:
a persistent socket and a fresh connection per poll were measured against
each other and scored identically, so the variable is uptime, not client
behaviour.

Home Assistant's response is to mark the entity `unavailable` until
somebody notices. The CozyLife phone app, meanwhile, keeps working the
whole time — because it does not use the local protocol at all. It talks
to CozyLife's cloud relay, an entirely separate path that stays up.

## What this does

Local first, cloud relay as fallback:

- **Local** (`api/local.py`) — the device's own listener. Fast, private,
  works with the internet down.
- **Cloud** (`api/cloud.py`) — CozyLife's relay, the same one the app
  uses. Slower and vendor-dependent, but alive when local is not.
- **Failover** (`api/fallback.py`) — prefers local, falls back on
  failure, and returns to local as soon as it recovers.

A device that answers and *refuses* a command is not retried on the other
path: it was reached, so the cloud cannot do better, and a retry would
risk driving the hardware twice.

The entity exposes a `connection_path` attribute reading `local` or
`cloud`. Failover is meant to be invisible, which is exactly what makes a
permanently-broken local path easy to miss.

## Address discovery

The account's device list returns each device's assigned *relay* address,
never its address on your network, so the local path needs discovery to
supply it. That runs over UDP (port 6095) rather than TCP, which matters:
the UDP responder keeps answering after the TCP listener has stopped, so
a device whose local control is dead can still be located. A DHCP lease
change also repairs itself on the next poll instead of stranding the
entity — otherwise local control would break permanently and invisibly,
with the cloud fallback quietly covering for it forever.

## Relationship to `polaralias/homeassistant-cozylife`

Independent implementation, with credit to that project as prior art for
the local protocol. No code is copied from it, and it is not a fork.

That was weighed seriously. For a single metering socket exposed as one
switch, its ~4,000 lines plus a 16,700-line device catalogue reduce to
roughly 250 relevant lines — the catalogue collapses to a single 20-line
object, its light and sensor platforms go unused, and its IP-range
scanning config flow is replaced entirely by the account's own device
list. Its architecture also resists a second transport: module-level
polling loops, entities mutating client internals, and four legacy config
shapes each platform re-flattens.

This integration uses the domain `cozylife_cloud`, not `cozylife`, so it
installs alongside that project rather than displacing it.

## Layout

```
custom_components/cozylife_cloud/
  api/            no Home Assistant imports — the wire protocol, testable alone
    protocol.py   frame encode/decode, shared by both transports
    wire.py       CRLF line framing over TCP
    local.py      the device's own listener on port 5555
    cloud.py      CozyLife's relay (subscribe-before-publish)
    fallback.py   local-first, cloud on failure
    rediscovering.py  local transport that follows a device that moves
    discovery.py  UDP device location
    account.py    login + device list, the only source of device_key
  config_flow.py  account sign-in, device choice, address fallback
  coordinator.py  polling and command dispatch
  switch.py       the switch entity
```

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check custom_components tests
```

The suite runs entirely against in-process fakes and is pinned to
loopback-only networking, so it cannot reach a real device or CozyLife
even by accident. See **`WRITE_SAFETY.md`** — read it before touching
anything that writes to a device.

## What must never end up in this repo, even in git history

- Any real `device_id`, `device_key`, account email, or account password.
- Any Home Assistant instance hostname, IP, or token.

Those live in the sibling private repo's tooling and the macOS Keychain,
deliberately kept out of this one because this repository is headed for a
**public** GitHub repository once the integration is confirmed working.
