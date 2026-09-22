# CozyLife for Home Assistant

A Home Assistant integration for CozyLife smart sockets that keeps working
when the device's local control path dies.

Local-first, with automatic fallback to CozyLife's cloud relay.

## Why this exists

CozyLife devices expose a local TCP control protocol on port 5555. On at
least some of their hardware **that listener leaks and stops accepting
connections after a few days of uptime**, and only a physical power-cycle
brings it back.

It is a firmware fault with no client-side fix. A persistent socket and a
fresh connection per poll were measured against each other and scored
identically, so it is not caused by how a client behaves.

Home Assistant's response is to mark the entity `unavailable` until somebody
notices. The CozyLife phone app, meanwhile, carries on — because it never
uses the local protocol at all. It talks to CozyLife's cloud relay, a
completely separate path that stays up.

This integration uses both.

## What it does

- **Local first.** Plain TCP to the device. Fast (~11 ms), private, and
  works with your internet down.
- **Cloud relay as fallback.** The same relay the phone app uses (~720 ms).
  Slower and vendor-dependent, but alive when the local listener is not.
- **Switches back on its own** as soon as local recovers.
- **A `connection_path` attribute** on the entity, reading `local` or
  `cloud`, so you can see which path is carrying traffic. Failover is meant
  to be invisible, which is exactly what makes a permanently broken local
  path easy to miss.
- **Metering sensors** on sockets that measure: power, voltage, current and
  cumulative energy. The energy sensor feeds Home Assistant's energy
  dashboard.

A **circuit breaker** is what makes local-first affordable. Once the
listener dies it stays dead for days, so without one every poll and every
button press would first wait out the local timeout. After three
consecutive failures the local path is skipped entirely and requests go
straight to the relay, with a probe every ten minutes to notice a
power-cycle. Measured on real hardware, that takes a read from ~1.8 s back
down to ~0.72 s — pure cloud latency.

### Supported devices

**Switches and sockets only.** The integration creates a single switch
entity per device, controlling datapoint 1.

You can add a CozyLife *bulb*, but you will get an on/off switch with no
brightness or colour control, and no sensors. If you only have lights, use
[`polaralias/homeassistant-cozylife`](https://github.com/polaralias/homeassistant-cozylife)
instead — it has proper light support, just no cloud fallback.

### What it does not fix

The entity still goes `unavailable` if **both** paths are down at once:

- the socket has no power, or has lost Wi-Fi
- your internet is down *and* the local listener is already dead
- the device was re-paired, so its stored key is stale — use
  **Reconfigure** to refresh it

You are covered whenever at least one path works. Notably, if local is
healthy an internet outage costs you nothing.

### The local listener cannot be revived remotely

Worth stating so nobody investigates it twice. Once the listener dies, only
a physical power-cycle brings it back:

- The device exposes exactly one TCP port, the dead one, plus UDP 6095.
  There is no web interface and no other management port.
- UDP 6095 is discovery-only. Every command number from 0 to 99 was swept;
  only 0, 2 and 3 exist at all, and all return `res:1` uniformly.
- The protocol's three commands are info, query and set. None reboots.
  A remote revival would therefore have to be a *datapoint* write.
- CozyLife's own app has no restart function, and a capture of its relay
  traffic contains no reboot-like command. If the vendor's app cannot do
  it, there is most likely nothing to find.
- Switching the relay off does not help: the Wi-Fi module is powered from
  mains directly, independent of the relay output.

This is why the integration exists. It does not fix the fault; it stops the
fault mattering.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install **CozyLife**, then restart Home Assistant

### Manual

Copy `custom_components/cozylife_cloud/` into your `config/custom_components/`
directory and restart Home Assistant.

## Setup

**Settings → Devices & Services → Add Integration → CozyLife.**

You are asked for your CozyLife account email and password. That is needed
for exactly one request: reading your account's device list, which is the
only place a device's control key (`device_key`) and its assigned relay
address can be obtained.

**Your password is not stored.** It is used for that single call and then
discarded; only the per-device key is written to the config entry.

The device's address on your network is found automatically over UDP. If it
does not answer — switched off, or on another subnet — you can enter its IP
by hand.

### Options

| Option | Default | Notes |
|---|---|---|
| Seconds between polls | 120 | Deliberately slow. Polling the local listener harder appears to bring the firmware fault on sooner. |
| Local connection timeout | 5 s | |
| Cloud relay timeout | 10 s | |

### If the device is re-paired

Re-pairing in the CozyLife app changes its control key. Use **Reconfigure**
on the integration to refresh it, rather than deleting and re-adding the
device — that keeps the entity id, and everything referring to it.

## Troubleshooting

**Which path is it using?** Check the `connection_path` attribute on the
entity. It is recorded, so you can also see transitions after the fact in
history.

**Nothing in the logs.** Home Assistant is near-silent by default, so this
integration's path-change lines do not appear. Add:

```yaml
logger:
  default: warning
  logs:
    custom_components.cozylife_cloud: info
```

You will then see lines like
`CozyLife switched from the local path to the cloud path`.

**The socket switches itself off a few seconds after every turn-on.**
That is the device's own countdown timer, not this integration. The
CozyLife app can arm a countdown that persists on the device and re-arms on
every turn-on, including turn-ons from Home Assistant. Clear it in the app,
or write datapoints `52` (countdown seconds) and `53` (enable) back to `0`.

**Device moved to a new IP.** Handled automatically. Discovery runs over
UDP, which keeps working even when the TCP listener is dead, so a DHCP
lease change repairs itself on the next poll.

## How it works

The wire protocol lives in `custom_components/cozylife_cloud/api/`, which
imports nothing from Home Assistant, so it can be tested on its own:

```
protocol.py       frame encode/decode, shared by both transports
wire.py           CRLF line framing over TCP
local.py          the device's own listener on port 5555
cloud.py          CozyLife's relay (subscribe-before-publish)
fallback.py       local-first, cloud on failure, with the circuit breaker
rediscovering.py  a local transport that follows a device that moves
discovery.py      UDP device location
account.py        login and device list — the only source of device_key
```

Two details are worth knowing if you read the code. The relay requires a
client to **subscribe to the report topic before publishing** a command, or
it has nowhere to route the reply and the answer simply never arrives. And
the local transport **holds its connection open**, reconnecting only when
the frame provably has not gone out — a command that may already have
reached the device must never be sent twice.

## Branding

Since Home Assistant 2026.3 a custom integration ships its own brand images
in `brand/`, and those take priority over the brands CDN.

The mark is CozyLife's own. It is their trademark, used here only to
identify the devices this integration talks to.

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check custom_components tests
```

The suite runs entirely against in-process fakes and is **pinned to
loopback-only networking**, so it cannot reach a real device or CozyLife's
servers even by accident.

**Read [`WRITE_SAFETY.md`](WRITE_SAFETY.md) before touching anything that
writes to a device.** These are mains-powered sockets; a stray write
switches real current.

## Credits

The local protocol was independently implemented, with credit to
[`polaralias/homeassistant-cozylife`](https://github.com/polaralias/homeassistant-cozylife)
as prior art. No code is copied from it and this is not a fork; it uses a
separate domain (`cozylife_cloud`) so the two can be installed side by side.

The cloud relay protocol was reverse-engineered from CozyLife's own Android
app. It is undocumented and unsupported, and may change without notice.

## Licence

[MIT](LICENSE).

The CozyLife name, and the mark in `custom_components/cozylife_cloud/brand/`,
are trademarks of their owner. They are not covered by the MIT licence and
are used here only to identify the devices this software talks to.
