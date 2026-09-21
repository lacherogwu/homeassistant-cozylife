"""The cloud relay transport: CozyLife's own relay, the path the phone app
uses. It keeps working while the device's local listener is dead, which is
the whole point of this integration.

Every test runs against an in-process fake relay. Nothing here contacts
CozyLife or any real device.
"""

import json
import urllib.parse

import pytest

from custom_components.cozylife_cloud.api import protocol
from custom_components.cozylife_cloud.api.cloud import CloudRelayTransport
from custom_components.cozylife_cloud.api.errors import TransportError

from .fakes import FakeLineServer, unused_port

# Deliberately fake. No real device id or key is ever committed here.
DEVICE_ID = "aaaabbbbccccdddd0001"
DEVICE_KEY = "test-key-not-a-real-one"


def parse_relay_line(line: bytes) -> dict[str, str]:
    fields = {}
    for part in line.decode().split("&"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key] = value
    return fields


def relay(state: dict[str, int], *, res: int = 0, subscribers: int = 1, answer: bool = True):
    """A relay that acks a subscribe, acks a publish, then delivers the
    device's report as a separate line -- exactly what the real one does."""

    def handler(line: bytes) -> list[bytes]:
        fields = parse_relay_line(line)

        if fields.get("cmd") == "subscribe":
            return [b"cmd=subscribe&res=1\r\n"]

        if fields.get("cmd") != "publish":
            return []

        ack = f"cmd=publish&res=1&num={subscribers}\r\n".encode()
        if not answer or subscribers == 0:
            return [ack]

        request = json.loads(urllib.parse.unquote(fields["message"]))
        report = protocol.encode(
            {
                "pv": 0,
                "cmd": request["cmd"],
                "sn": request["sn"],
                "res": res,
                "msg": {"attr": [int(k) for k in state], "data": state},
            }
        )[:-2]
        delivered = (
            f"cmd=publish&topic=device_{DEVICE_ID}&device_id={DEVICE_ID}"
            f"&message={urllib.parse.quote(report.decode(), safe='')}\r\n"
        ).encode()
        return [ack, delivered]

    return handler


def make_transport(server, **kwargs) -> CloudRelayTransport:
    return CloudRelayTransport(
        DEVICE_ID, DEVICE_KEY, server.host, server.port, timeout=2, **kwargs
    )


def test_query_returns_the_datapoint_map_from_the_devices_report():
    with FakeLineServer(relay({"1": 1, "26": 263})) as server:
        assert make_transport(server).query() == {"1": 1, "26": 263}


def test_it_subscribes_before_it_publishes():
    """Load-bearing: without a prior subscribe to the report topic the relay
    has nowhere to route the device's reply, and it never arrives."""

    with FakeLineServer(relay({"1": 1})) as server:
        make_transport(server).query()

        verbs = [parse_relay_line(line)["cmd"] for line in server.received]
        assert verbs == ["subscribe", "publish"]


def test_it_subscribes_to_the_report_topic_and_publishes_to_the_control_topic():
    with FakeLineServer(relay({"1": 1})) as server:
        make_transport(server).query()

        subscribe, publish = (parse_relay_line(line) for line in server.received)
        assert subscribe["topic"] == f"device_{DEVICE_ID}"
        assert publish["topic"] == f"control_{DEVICE_ID}"


def test_the_published_message_is_the_url_encoded_device_frame():
    with FakeLineServer(relay({"1": 1})) as server:
        make_transport(server).query()

        publish = parse_relay_line(server.received[1])
        frame = json.loads(urllib.parse.unquote(publish["message"]))
        assert frame["cmd"] == protocol.CMD_QUERY
        assert frame["msg"] == {"attr": [0]}


def test_every_line_carries_the_device_credentials():
    with FakeLineServer(relay({"1": 1})) as server:
        make_transport(server).query()

        for line in server.received:
            fields = parse_relay_line(line)
            assert fields["device_id"] == DEVICE_ID
            assert fields["device_key"] == DEVICE_KEY


def test_control_sends_a_set_frame_and_reports_success():
    with FakeLineServer(relay({"1": 0})) as server:
        assert make_transport(server).control({"1": 0}) is True

        publish = parse_relay_line(server.received[1])
        frame = json.loads(urllib.parse.unquote(publish["message"]))
        assert frame["cmd"] == protocol.CMD_SET
        assert frame["msg"]["data"] == {"1": 0}


def test_control_reports_failure_when_the_device_rejects_the_command():
    with FakeLineServer(relay({"1": 0}, res=1)) as server:
        assert make_transport(server).control({"1": 0}) is False


def test_the_publish_ack_is_not_mistaken_for_the_devices_answer():
    """The relay acks delivery immediately; the device's real reply is a
    separate line arriving later. Returning on the ack would report stale
    state as if it were fresh."""

    with FakeLineServer(relay({"1": 7})) as server:
        assert make_transport(server).query() == {"1": 7}


def test_delivery_to_zero_subscribers_fails_fast_with_a_clear_message():
    """num=0 means the relay accepted the line and delivered it to nobody --
    the device is offline, or registered on a different relay port. Waiting
    out the full timeout hides a misconfiguration that this names outright."""

    with FakeLineServer(relay({"1": 1}, subscribers=0)) as server:
        with pytest.raises(TransportError, match="no subscriber"):
            make_transport(server).query()


def test_a_report_for_another_device_is_ignored():
    def handler(line: bytes) -> list[bytes]:
        fields = parse_relay_line(line)
        if fields.get("cmd") == "subscribe":
            return [b"cmd=subscribe&res=1\r\n"]
        if fields.get("cmd") != "publish":
            return []
        request = json.loads(urllib.parse.unquote(fields["message"]))
        other = protocol.encode(
            {"pv": 0, "cmd": 2, "sn": request["sn"], "res": 0, "msg": {"data": {"1": 99}}}
        )[:-2]
        mine = protocol.encode(
            {"pv": 0, "cmd": 2, "sn": request["sn"], "res": 0, "msg": {"data": {"1": 1}}}
        )[:-2]
        return [
            b"cmd=publish&res=1&num=1\r\n",
            f"cmd=publish&topic=device_9999999999&device_id=9999999999"
            f"&message={urllib.parse.quote(other.decode(), safe='')}\r\n".encode(),
            f"cmd=publish&topic=device_{DEVICE_ID}&device_id={DEVICE_ID}"
            f"&message={urllib.parse.quote(mine.decode(), safe='')}\r\n".encode(),
        ]

    with FakeLineServer(handler) as server:
        assert make_transport(server).query() == {"1": 1}


def test_an_unreachable_relay_raises_transport_error():
    transport = CloudRelayTransport(
        DEVICE_ID, DEVICE_KEY, "127.0.0.1", unused_port(), timeout=1
    )

    with pytest.raises(TransportError):
        transport.query()


def test_a_relay_that_never_delivers_a_report_raises_transport_error():
    with FakeLineServer(relay({"1": 1}, answer=False)) as server:
        transport = CloudRelayTransport(
            DEVICE_ID, DEVICE_KEY, server.host, server.port, timeout=0.5
        )
        with pytest.raises(TransportError):
            transport.query()


def test_the_device_key_never_appears_in_the_repr_or_in_an_error():
    """The key is the literal control credential for the socket. It must not
    reach a log line or a HA error dialog."""

    transport = CloudRelayTransport(
        DEVICE_ID, DEVICE_KEY, "127.0.0.1", unused_port(), timeout=0.5
    )

    assert DEVICE_KEY not in repr(transport)
    with pytest.raises(TransportError) as caught:
        transport.query()
    assert DEVICE_KEY not in str(caught.value)
