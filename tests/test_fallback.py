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
