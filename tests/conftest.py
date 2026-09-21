"""Shared test setup.

Two jobs: make ``custom_components`` importable, and pin the test suite to
loopback-only networking.

That second one enforces WRITE_SAFETY.md mechanically rather than by good
intentions. The Home Assistant test harness blocks sockets outright; this
re-enables them for 127.0.0.1 only, so the in-process fakes work while any
test that reaches for a real device or for CozyLife's cloud fails loudly
instead of quietly doing it.
"""

import sys
from pathlib import Path

import pytest
import pytest_socket

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOOPBACK = ["127.0.0.1", "::1", "localhost"]


@pytest.fixture(autouse=True)
def loopback_only_networking(socket_enabled):
    """Allow sockets, but only to the local machine."""

    pytest_socket.socket_allow_hosts(LOOPBACK, allow_unix_socket=True)
    yield
    pytest_socket.disable_socket(allow_unix_socket=True)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this repo's integration during tests."""

    yield
