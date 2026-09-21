"""A local transport that notices when the device changes address.

Without this, a DHCP lease change breaks local control permanently and
invisibly: the cloud fallback keeps the entity working, so nothing looks
wrong, while every request quietly takes the slow vendor path forever.
"""

import pytest

from custom_components.cozylife_cloud.api.errors import TransportError
from custom_components.cozylife_cloud.api.rediscovering import (
    RediscoveringLocalTransport,
)

DEVICE_ID = "aaaabbbbccccdddd0001"


class StubLocal:
    """A local transport bound to one address, as the real one is."""

    def __init__(self, host, reachable_at):
        self.host = host
        self._reachable_at = reachable_at
        self.queries = 0
        self.controls = []

    @property
    def name(self):
        return "local"

    def query(self, attrs=None):
        self.queries += 1
        if self.host != self._reachable_at:
            raise TransportError(f"cannot connect to {self.host}")
        return {"1": 1}

    def control(self, payload):
        self.controls.append(payload)
        if self.host != self._reachable_at:
            raise TransportError(f"cannot connect to {self.host}")
        return True


def build(host, reachable_at, found_at=None, *, min_interval=0.0):
    """Wire a transport whose device is reachable at ``reachable_at`` and
    which discovery reports at ``found_at``."""

    built = []
    calls = []

    def factory(h):
        transport = StubLocal(h, reachable_at)
        built.append(transport)
        return transport

    def rediscover(device_id):
        calls.append(device_id)
        return found_at

    transport = RediscoveringLocalTransport(
        DEVICE_ID,
        host,
        transport_factory=factory,
        rediscover=rediscover,
        min_rediscovery_interval=min_interval,
    )
    return transport, built, calls


def test_it_passes_a_working_query_straight_through():
    transport, _, _ = build("192.0.2.5", reachable_at="192.0.2.5")

    assert transport.query() == {"1": 1}


def test_it_does_not_go_looking_while_the_device_answers():
    """Discovery is a LAN-wide broadcast. It is not free, and a healthy
    device is not lost."""

    transport, _, calls = build("192.0.2.5", reachable_at="192.0.2.5")
    transport.query()

    assert calls == []


def test_a_device_that_moved_is_found_and_the_query_retried():
    transport, _, calls = build(
        "192.0.2.5", reachable_at="192.0.2.9", found_at="192.0.2.9"
    )

    assert transport.query() == {"1": 1}
    assert calls == [DEVICE_ID]
    assert transport.host == "192.0.2.9"


def test_the_new_address_is_kept_for_later_requests():
    transport, _, calls = build(
        "192.0.2.5", reachable_at="192.0.2.9", found_at="192.0.2.9"
    )
    transport.query()
    transport.query()

    assert calls == [DEVICE_ID]  # the second query needed no search


def test_a_dead_listener_at_an_unchanged_address_is_not_retried():
    """Discovery answering with the address we already have means the device
    is there but its TCP listener is dead -- the firmware fault. Retrying
    the same address cannot help; the caller should fail over instead."""

    transport, built, _ = build(
        "192.0.2.5", reachable_at="10.0.0.1", found_at="192.0.2.5"
    )

    with pytest.raises(TransportError):
        transport.query()

    assert sum(t.queries for t in built) == 1


def test_a_device_that_answers_nothing_raises_the_original_error():
    transport, _, _ = build("192.0.2.5", reachable_at="10.0.0.1", found_at=None)

    with pytest.raises(TransportError, match=r"192\.0\.2\.5"):
        transport.query()


def test_searching_is_rate_limited_across_failures():
    """The listener stays dead for days. Broadcasting on every poll for all
    that time would be antisocial to the rest of the network."""

    transport, _, calls = build(
        "192.0.2.5", reachable_at="10.0.0.1", found_at=None, min_interval=60.0
    )

    for _ in range(4):
        with pytest.raises(TransportError):
            transport.query()

    assert len(calls) == 1


def test_a_working_write_passes_straight_through():
    transport, _, _ = build("192.0.2.5", reachable_at="192.0.2.5")

    assert transport.control({"1": 0}) is True


def test_a_failed_write_is_never_retried_locally():
    """A write that failed may still have reached the device. Re-sending it
    to a rediscovered address risks driving the hardware twice, and the
    caller has a cloud path that is known to work."""

    transport, built, _ = build(
        "192.0.2.5", reachable_at="192.0.2.9", found_at="192.0.2.9"
    )

    with pytest.raises(TransportError):
        transport.control({"1": 0})

    assert [c for t in built for c in t.controls] == [{"1": 0}]


def test_a_failed_write_still_refreshes_the_address_for_next_time():
    """Writes are rare and polls are constant, but a write should not leave
    a known-stale address in place."""

    transport, _, _ = build(
        "192.0.2.5", reachable_at="192.0.2.9", found_at="192.0.2.9"
    )

    with pytest.raises(TransportError):
        transport.control({"1": 0})

    assert transport.host == "192.0.2.9"


def test_it_reports_itself_as_the_local_path():
    transport, _, _ = build("192.0.2.5", reachable_at="192.0.2.5")

    assert transport.name == "local"


def test_closing_it_closes_the_underlying_transport():
    closed = []

    class ClosableLocal(StubLocal):
        def close(self):
            closed.append(self.host)

    transport = RediscoveringLocalTransport(
        DEVICE_ID,
        "192.0.2.5",
        transport_factory=lambda h: ClosableLocal(h, "192.0.2.5"),
        rediscover=lambda _d: None,
    )
    transport.close()

    assert closed == ["192.0.2.5"]


def test_replacing_a_moved_devices_transport_closes_the_old_one():
    """Otherwise a device that changes address leaks the socket held to the
    old one."""

    closed = []

    class ClosableLocal(StubLocal):
        def close(self):
            closed.append(self.host)

    transport = RediscoveringLocalTransport(
        DEVICE_ID,
        "192.0.2.5",
        transport_factory=lambda h: ClosableLocal(h, "192.0.2.9"),
        rediscover=lambda _d: "192.0.2.9",
        min_rediscovery_interval=0.0,
    )
    transport.query()

    assert closed == ["192.0.2.5"]
