"""The local transport: plain TCP to the device's own listener on port 5555.

This is the fast path. It is also the one that is known to die every few
days, which is the entire reason the cloud fallback exists -- so the
failure modes below are tested as carefully as the happy path.
"""

import json

import pytest

from custom_components.cozylife_cloud.api import protocol
from custom_components.cozylife_cloud.api.errors import TransportError
from custom_components.cozylife_cloud.api.local import LocalTransport

from .fakes import FakeLineServer, unused_port


def echo_state(state: dict[str, int], *, res: int = 0):
    """Reply to any frame with the device's stock response shape."""

    def handler(line: bytes) -> list[bytes]:
        request = json.loads(line)
        return [
            protocol.encode(
                {
                    "pv": 0,
                    "cmd": request["cmd"],
                    "sn": request["sn"],
                    "res": res,
                    "msg": {"attr": [int(k) for k in state], "data": state},
                }
            )
        ]

    return handler


def test_query_returns_the_devices_datapoint_map():
    with FakeLineServer(echo_state({"1": 1, "26": 263})) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)

        assert transport.query() == {"1": 1, "26": 263}


def test_query_sends_a_wildcard_query_frame():
    with FakeLineServer(echo_state({"1": 1})) as server:
        LocalTransport(server.host, port=server.port, timeout=2).query()

        sent = json.loads(server.received[0])
        assert sent["cmd"] == protocol.CMD_QUERY
        assert sent["msg"] == {"attr": [0]}


def test_control_sends_a_set_frame_and_reports_success():
    with FakeLineServer(echo_state({"1": 0})) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)

        assert transport.control({"1": 0}) is True
        sent = json.loads(server.received[0])
        assert sent["cmd"] == protocol.CMD_SET
        assert sent["msg"]["data"] == {"1": 0}


def test_control_reports_failure_when_the_device_rejects_the_command():
    with FakeLineServer(echo_state({"1": 0}, res=1)) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)

        assert transport.control({"1": 0}) is False


def test_a_reply_for_a_different_command_is_skipped():
    """Unsolicited cmd:10 reports interleave with replies; the right one wins."""

    def handler(line: bytes) -> list[bytes]:
        request = json.loads(line)
        return [
            protocol.encode(
                {"pv": 0, "cmd": 10, "sn": "999", "msg": {"data": {"1": 9}}}
            ),
            protocol.encode(
                {
                    "pv": 0,
                    "cmd": 2,
                    "sn": request["sn"],
                    "res": 0,
                    "msg": {"data": {"1": 1}},
                }
            ),
        ]

    with FakeLineServer(handler) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)

        assert transport.query() == {"1": 1}


def test_two_frames_arriving_in_one_segment_are_both_parsed():
    """The device coalesces writes; a naive one-recv-per-frame reader breaks."""

    def handler(line: bytes) -> list[bytes]:
        request = json.loads(line)
        noise = protocol.encode({"pv": 0, "cmd": 10, "sn": "999", "msg": {}})
        answer = protocol.encode(
            {"pv": 0, "cmd": 2, "sn": request["sn"], "res": 0, "msg": {"data": {"1": 1}}}
        )
        return [noise + answer]  # one sendall, two frames

    with FakeLineServer(handler) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)

        assert transport.query() == {"1": 1}


def test_a_refused_connection_raises_transport_error():
    transport = LocalTransport("127.0.0.1", port=unused_port(), timeout=1)

    with pytest.raises(TransportError):
        transport.query()


def test_a_listener_that_accepts_but_never_answers_raises_transport_error():
    """The real firmware failure: the socket opens, nothing ever comes back."""

    with FakeLineServer(lambda line: []) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=0.5)

        with pytest.raises(TransportError):
            transport.query()


def test_each_call_uses_its_own_connection():
    """Stateless calls: a socket left over from a dead listener can't poison
    the next poll, and there is no shared state to lock."""

    with FakeLineServer(echo_state({"1": 1})) as server:
        transport = LocalTransport(server.host, port=server.port, timeout=2)
        transport.query()
        transport.query()

        assert len(server.received) == 2
