# homeassistant-cozylife-cloud

**Status: private development. Not yet published. Do not push this repo
to GitHub until explicitly told to — see `WORKING_NOTES.md`.**

A Home Assistant custom integration adding **cloud-relay fallback** for
CozyLife devices, for the case where the device's local TCP listener
(port 5555) becomes unreliable or dies — a real firmware bug in at least
some CozyLife hardware, not something fixable client-side. When local
control fails, this falls back to the same cloud relay the official
CozyLife phone app uses, instead of leaving the entity `unavailable`
until someone power-cycles the device.

Builds on [`polaralias/homeassistant-cozylife`](https://github.com/polaralias/homeassistant-cozylife),
which already implements solid local-protocol control; this project is
additive (a fork extended with a cloud fallback path), not a from-scratch
reimplementation. That decision — fork vs. a separate integration — is
one of the first things to confirm when picking this back up; see
`WORKING_NOTES.md`.

## Before touching this repo

Read, in order:

1. `WORKING_NOTES.md` in this repo — working agreements specific to this
   project (when it's safe to push, how credentials are handled, the
   physical-device safety rule).
2. The full technical design doc — protocol details, every endpoint,
   every gotcha found reverse-engineering CozyLife's cloud API, and the
   implementation plan:
   `~/REDACTED/Projects/REDACTED/docs/superpowers/specs/2026-09-21-cozylife-cloud-relay-design.md`
   (private, sibling repo — not part of this codebase, not published;
   read it locally for context, don't copy its contents in here verbatim
   since it references one specific user's device IDs).
3. The existing local-protocol integration's actual source, both the
   public repo linked above and the deployed copy this project's owner
   is running, for the patterns and conventions to match (manifest.json
   shape, entity structure, dpid handling): `custom_components/cozylife/`
   on their Home Assistant instance.

## What must never end up in this repo, even in git history

- Any real `device_id`, `device_key`, account email, or account password
  belonging to the project owner's actual CozyLife account.
- Any Home Assistant instance hostname, IP, or token.

Those live in the sibling `REDACTED` repo's tooling
(`tools/cozylife/`) and the local macOS Keychain, deliberately kept out
of this one because this repo is headed for a **public** GitHub
repository once the integration is confirmed working.
