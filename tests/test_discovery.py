"""UDP discovery: finding a device's current LAN address.

The cloud device list gives the *relay* address, never the device's own.
Discovery supplies the missing half, and it matters that it runs over UDP:
the responder on port 6095 keeps answering after the device's TCP listener
has stopped, which is precisely the situation this integration exists for.
It also means a DHCP lease change repairs itself.
"""

import json

from custom_components.cozylife_cloud.api import discovery

from .fakes import FakeUdpResponder

DEVICE_ID = "aaaabbbbccccdddd0001"


def response(device_id=DEVICE_ID, ip="192.0.2.50", **extra) -> bytes:
    message = {
        "did": device_id,
        "dtp": "02",
        "pid": "TESTPD",
        "mac": "aabbccddeeff",
        "ip": ip,
        "rssi": -52,
        "sv": "1.0.3",
        "hv": "0.0.1",
    }
    message.update(extra)
    return json.dumps({"cmd": 0, "pv": 0, "sn": "1", "msg": message, "res": 0}).encode()


def discover(responder, **kwargs):
    return discovery.discover(
        targets=["127.0.0.1"], port=responder.port, timeout=0.3, **kwargs
    )


def test_it_reports_a_responding_devices_id_and_address():
    with FakeUdpResponder([response()]) as responder:
        found = discover(responder)

        assert len(found) == 1
        assert found[0].device_id == DEVICE_ID
        assert found[0].ip == "192.0.2.50"


def test_it_reports_the_firmware_versions_the_device_advertises():
    with FakeUdpResponder([response()]) as responder:
        device = discover(responder)[0]

        assert device.software_version == "1.0.3"
        assert device.hardware_version == "0.0.1"
        assert device.mac == "aabbccddeeff"


def test_it_probes_with_a_read_only_info_command():
    """Discovery must never be able to change device state. cmd:0 reads
    metadata; anything else broadcast to every device on the LAN would be
    reckless."""

    with FakeUdpResponder([response()]) as responder:
        discover(responder)

        probe = json.loads(responder.received[0])
        assert probe["cmd"] == 0
        assert probe["msg"] == {}


def test_it_can_look_up_one_devices_address_by_id():
    with FakeUdpResponder([response()]) as responder:
        found = discovery.find_device_ip(
            DEVICE_ID, targets=["127.0.0.1"], port=responder.port, timeout=0.3
        )

        assert found == "192.0.2.50"


def test_looking_up_an_absent_device_returns_nothing():
    with FakeUdpResponder([response(device_id="ffffffffffffffff9999")]) as responder:
        found = discovery.find_device_ip(
            DEVICE_ID, targets=["127.0.0.1"], port=responder.port, timeout=0.3
        )

        assert found is None


def test_it_falls_back_to_the_senders_address_when_the_body_omits_one():
    with FakeUdpResponder([response(ip=None)]) as responder:
        device = discover(responder)[0]

        assert device.ip == "127.0.0.1"


def test_a_device_answering_twice_is_reported_once():
    with FakeUdpResponder([response(), response()]) as responder:
        assert len(discover(responder)) == 1


def test_a_malformed_reply_does_not_break_the_scan():
    with FakeUdpResponder([b"<not json>", response()]) as responder:
        assert [d.device_id for d in discover(responder)] == [DEVICE_ID]


def test_a_reply_without_a_device_id_is_ignored():
    noise = json.dumps({"cmd": 0, "msg": {"ip": "192.0.2.9"}}).encode()
    with FakeUdpResponder([noise, response()]) as responder:
        assert [d.device_id for d in discover(responder)] == [DEVICE_ID]


def test_a_silent_network_yields_no_devices():
    with FakeUdpResponder([]) as responder:
        assert discover(responder) == []


def test_looking_up_a_device_returns_as_soon_as_it_answers():
    """A poll that has just lost the local path waits on this before it can
    retry, so it must not sit out the whole scan window once it has what it
    came for."""

    import time

    with FakeUdpResponder([response()]) as responder:
        started = time.monotonic()
        discovery.find_device_ip(
            DEVICE_ID, targets=["127.0.0.1"], port=responder.port, timeout=5.0
        )
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
