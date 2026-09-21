"""Local-first, cloud-fallback orchestration.

This is the feature. The device's local listener dies every few days; when
it does, every call to it raises and the cloud relay has to pick up
transparently, without the entity ever going unavailable.
"""

import logging

import pytest

from custom_components.cozylife_cloud.api.errors import TransportError
from custom_components.cozylife_cloud.api.fallback import FallbackClient


class StubTransport:
    """Stands in for a real transport. Both real ones are covered by their
    own socket-level tests; what matters here is which one gets called."""

    def __init__(self, name, *, data=None, accepts=True, broken=False):
        self.name = name
        self._data = data or {"1": 1}
        self._accepts = accepts
        self.broken = broken
        self.queries = 0
        self.controls = []

    def query(self, attrs=None):
        self.queries += 1
        if self.broken:
            raise TransportError(f"{self.name} is unreachable")
        return self._data

    def control(self, payload):
        self.controls.append(payload)
        if self.broken:
            raise TransportError(f"{self.name} is unreachable")
        return self._accepts


def make(local=None, cloud=None):
    return FallbackClient(
        local if local is not None else StubTransport("local"),
        cloud if cloud is not None else StubTransport("cloud"),
    )


def test_a_healthy_local_transport_serves_the_query():
    local = StubTransport("local", data={"1": 1})
    cloud = StubTransport("cloud", data={"1": 0})

    assert make(local, cloud).query() == {"1": 1}


def test_the_cloud_is_not_touched_while_local_works():
    """Local-first is not just a preference: every avoided cloud call is one
    fewer vendor round-trip and one fewer thing to leak credentials to."""

    local, cloud = StubTransport("local"), StubTransport("cloud")
    make(local, cloud).query()

    assert cloud.queries == 0


def test_a_dead_local_listener_fails_over_to_the_cloud():
    local = StubTransport("local", broken=True)
    cloud = StubTransport("cloud", data={"1": 1, "26": 263})

    assert make(local, cloud).query() == {"1": 1, "26": 263}
    assert cloud.queries == 1


def test_a_write_fails_over_to_the_cloud_too():
    local = StubTransport("local", broken=True)
    cloud = StubTransport("cloud")

    assert make(local, cloud).control({"1": 0}) is True
    assert cloud.controls == [{"1": 0}]


def test_a_device_that_refuses_a_command_does_not_trigger_failover():
    """A refusal means the device was reached and said no. The cloud cannot
    do better, and retrying there would send the command to hardware twice."""

    local = StubTransport("local", accepts=False)
    cloud = StubTransport("cloud")

    assert make(local, cloud).control({"1": 0}) is False
    assert cloud.controls == []


def test_both_paths_down_raises_so_the_entity_can_go_unavailable():
    client = make(
        StubTransport("local", broken=True), StubTransport("cloud", broken=True)
    )

    with pytest.raises(TransportError):
        client.query()


def test_the_error_when_both_fail_names_both_causes():
    client = make(
        StubTransport("local", broken=True), StubTransport("cloud", broken=True)
    )

    with pytest.raises(TransportError) as caught:
        client.query()

    assert "local is unreachable" in str(caught.value)
    assert "cloud is unreachable" in str(caught.value)


def test_it_reports_which_path_served_the_last_request():
    local = StubTransport("local", broken=True)
    client = make(local, StubTransport("cloud"))
    client.query()

    assert client.active_path == "cloud"


def test_it_returns_to_local_once_the_listener_recovers():
    """A power-cycle brings the listener back; the client must notice rather
    than staying on the cloud until Home Assistant restarts."""

    local = StubTransport("local", broken=True)
    client = make(local, StubTransport("cloud"))
    client.query()

    local.broken = False
    client.query()

    assert client.active_path == "local"


def test_switching_path_is_logged_at_info(caplog):
    local = StubTransport("local", broken=True)
    client = make(local, StubTransport("cloud"))

    with caplog.at_level(logging.INFO):
        client.query()

    assert "cloud" in caplog.text


def test_staying_on_one_path_does_not_log_every_poll(caplog):
    """Polling runs all day. Only transitions are worth an INFO line."""

    client = make(StubTransport("local"), StubTransport("cloud"))

    with caplog.at_level(logging.INFO):
        for _ in range(5):
            client.query()

    assert len(caplog.records) == 1


def test_it_works_with_no_cloud_configured():
    """Cloud credentials are optional; without them this is local-only and
    must still behave, rather than crashing on a missing transport."""

    client = FallbackClient(StubTransport("local", data={"1": 1}), None)

    assert client.query() == {"1": 1}


def test_without_a_cloud_a_dead_listener_simply_raises():
    client = FallbackClient(StubTransport("local", broken=True), None)

    with pytest.raises(TransportError):
        client.query()


class Clock:
    """A hand-cranked monotonic clock, so breaker timings are exact."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def with_breaker(local, cloud, *, threshold=3, recovery=600.0, clock=None):
    return FallbackClient(
        local,
        cloud,
        failure_threshold=threshold,
        recovery_interval=recovery,
        monotonic=clock or Clock(),
    )


def test_an_occasional_local_failure_does_not_stop_us_trying_local():
    """A single dropped poll is not the firmware fault. Giving up on local
    after one blip would send months of traffic to the cloud needlessly."""

    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud"), threshold=3)

    client.query()
    local.broken = False
    client.query()

    assert local.queries == 2
    assert client.active_path == "local"


def test_repeated_local_failures_stop_it_being_tried_at_all():
    """Once the listener is gone it stays gone for days. Paying its full
    timeout on every poll and every button press for that whole period is
    the real cost of local-first, and this is what removes it."""

    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud"), threshold=3)

    for _ in range(3):
        client.query()
    attempts_before = local.queries

    client.query()
    client.query()

    assert local.queries == attempts_before
    assert client.local_circuit_open is True


def test_requests_are_still_served_while_local_is_cut_out():
    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud", data={"1": 1}), threshold=2)

    for _ in range(4):
        result = client.query()

    assert result == {"1": 1}
    assert client.active_path == "cloud"


def test_local_is_probed_again_once_the_recovery_interval_passes():
    """A power-cycle brings the listener back. Nothing tells us when, so the
    only way to notice is to try again periodically."""

    clock = Clock()
    local = StubTransport("local", broken=True)
    client = with_breaker(
        local, StubTransport("cloud"), threshold=2, recovery=600.0, clock=clock
    )

    for _ in range(3):
        client.query()
    attempts_while_open = local.queries

    clock.advance(601)
    client.query()

    assert local.queries == attempts_while_open + 1


def test_a_successful_probe_puts_local_back_in_service():
    clock = Clock()
    local = StubTransport("local", broken=True)
    client = with_breaker(
        local, StubTransport("cloud"), threshold=2, recovery=600.0, clock=clock
    )
    for _ in range(3):
        client.query()

    local.broken = False
    clock.advance(601)
    client.query()

    assert client.local_circuit_open is False
    assert client.active_path == "local"


def test_a_failed_probe_waits_out_another_interval():
    """Otherwise every subsequent request would probe again and the timeout
    penalty would be back."""

    clock = Clock()
    local = StubTransport("local", broken=True)
    client = with_breaker(
        local, StubTransport("cloud"), threshold=2, recovery=600.0, clock=clock
    )
    for _ in range(3):
        client.query()

    clock.advance(601)
    client.query()
    attempts_after_probe = local.queries

    client.query()
    clock.advance(10)
    client.query()

    assert local.queries == attempts_after_probe


def test_a_local_success_clears_the_tally_of_earlier_failures():
    """Failures have to be consecutive to mean anything; intermittent ones
    spread over weeks should not eventually add up to tripping the breaker."""

    clock = Clock()
    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud"), threshold=3, clock=clock)

    client.query()
    client.query()
    local.broken = False
    client.query()
    local.broken = True
    client.query()
    client.query()

    assert client.local_circuit_open is False


def test_writes_also_stop_paying_the_dead_local_timeout():
    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud"), threshold=2)

    for _ in range(3):
        client.query()
    controls_before = len(local.controls)

    assert client.control({"1": 0}) is True
    assert len(local.controls) == controls_before


def test_without_a_cloud_path_local_is_never_cut_out():
    """There would be nothing left to serve the request. A slow answer beats
    no answer."""

    local = StubTransport("local", broken=True)
    client = FallbackClient(local, None, failure_threshold=2)

    for _ in range(5):
        with pytest.raises(TransportError):
            client.query()

    assert local.queries == 5
    assert client.local_circuit_open is False


def test_cutting_local_out_is_logged_at_info(caplog):
    local = StubTransport("local", broken=True)
    client = with_breaker(local, StubTransport("cloud"), threshold=2)

    with caplog.at_level(logging.INFO):
        for _ in range(2):
            client.query()

    assert "local" in caplog.text.lower()


def test_closing_the_client_releases_the_local_connection():
    """The local transport now holds a socket open. Leaving it dangling
    across a config entry reload would leak one per reload -- on a device
    whose whole problem is thought to be leaked connections."""

    class ClosableTransport(StubTransport):
        def __init__(self, name):
            super().__init__(name)
            self.closed = False

        def close(self):
            self.closed = True

    local = ClosableTransport("local")
    client = FallbackClient(local, StubTransport("cloud"))

    client.close()

    assert local.closed is True


def test_closing_a_client_whose_transport_cannot_be_closed_is_harmless():
    """Not every transport holds a resource; the cloud one does not."""

    FallbackClient(StubTransport("local"), StubTransport("cloud")).close()
