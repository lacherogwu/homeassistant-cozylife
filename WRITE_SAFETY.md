# Write safety

## The rule

**Never send a device-control command to real hardware without the project
owner physically present and watching the device.**

In this codebase a "control command" means anything that reaches
`protocol.set_frame()` — the device protocol's `cmd:3` — over either
transport. `LocalTransport.control()` and `CloudRelayTransport.control()`
are the only two functions that can do it.

Reads (`cmd:2`, `query()`) are unrestricted. They cannot change device
state, and probing the cloud API with a deliberately wrong password is
likewise safe and is how several protocol details were established without
ever using real credentials.

## Why it is written down rather than assumed

The device is a mains-powered socket. A stray write does not print a wrong
number on a screen; it switches real current to whatever is plugged in,
possibly in an empty house. "It should have been a no-op" is not a thing
you can verify after the fact.

Both live write tests done so far (2026-09-21, dpid 1 → 0, then → 1) were
run one step at a time with the owner confirming the physical result before
the next step. That is the standard, and it does not relax because the code
path is now covered by tests.

## What this means for the test suite

Every test in `tests/` runs against an in-process fake — `FakeLineServer`
for both transports, `FakeHttpServer` for the cloud API. No test opens a
socket to anything but `127.0.0.1`, and the suite works with the network
off.

Concretely, when adding tests:

- Never import real credentials, and never read the Keychain from a test.
- Use the obviously-fake device ids already in the suite
  (`aaaabbbbccccdddd0001`) and keys (`test-key-not-a-real-one`).
- If a test seems to need a real device to be meaningful, it is an
  integration check, not a unit test. It belongs in a manual, supervised
  session — not in `pytest`.

## Credentials

No real `device_id`, `device_key`, account email, account password, or
Home Assistant hostname/token belongs in this repo — not in code, not in a
comment, not in a fixture, and not in git history, since this repository is
headed for a public GitHub remote.

While developing against the real device, use the Mac-side tooling in the
sibling private repo (`tools/cozylife/`), which keeps device keys in the
macOS Keychain and prompts for the account password interactively.

At runtime the integration itself never persists the account password: the
config flow exchanges it for a session token once and stores only the
token and the per-device key, in Home Assistant's own config entry storage.
