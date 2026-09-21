"""Frame encoding for the CozyLife device protocol.

The same frame shape travels over both transports: the device's local TCP
listener speaks it directly, and the cloud relay carries it as the
url-encoded ``message`` field of a relay line. Getting it wrong breaks
both paths at once, so it is pinned down here on its own.
"""

import json

from custom_components.cozylife_cloud.api import protocol


def test_query_frame_asks_for_every_attribute_by_default():
    frame = protocol.query_frame(sn="1700000000000")

    assert frame == {
        "pv": 0,
        "cmd": 2,
        "sn": "1700000000000",
        "msg": {"attr": [0]},
    }


def test_query_frame_can_ask_for_specific_attributes():
    frame = protocol.query_frame(attrs=[1, 26], sn="1700000000000")

    assert frame["msg"] == {"attr": [1, 26]}


def test_set_frame_derives_its_attr_list_from_the_payload_keys():
    frame = protocol.set_frame({"1": 0}, sn="1700000000000")

    assert frame == {
        "pv": 0,
        "cmd": 3,
        "sn": "1700000000000",
        "msg": {"attr": [1], "data": {"1": 0}},
    }


def test_set_frame_accepts_int_keys_and_normalises_them_to_strings():
    # The device rejects a data map keyed by ints; HA-side callers naturally
    # reach for ints, so the boundary normalises rather than trusting callers.
    frame = protocol.set_frame({1: 1}, sn="1700000000000")

    assert frame["msg"] == {"attr": [1], "data": {"1": 1}}


def test_encode_is_compact_json_terminated_by_crlf():
    raw = protocol.encode({"pv": 0, "cmd": 2, "sn": "1", "msg": {}})

    assert raw.endswith(b"\r\n")
    assert b", " not in raw and b'": ' not in raw  # no whitespace padding
    assert json.loads(raw[:-2]) == {"pv": 0, "cmd": 2, "sn": "1", "msg": {}}


def test_decode_reads_back_what_encode_wrote():
    frame = protocol.set_frame({"1": 1}, sn="42")

    assert protocol.decode(protocol.encode(frame)) == frame


def test_decode_returns_none_for_a_non_json_line():
    assert protocol.decode(b"not json at all\r\n") is None


def test_decode_returns_none_for_json_that_is_not_a_frame_object():
    # The relay occasionally emits bare values; they must not crash a poll.
    assert protocol.decode(b"[1, 2, 3]\r\n") is None


def test_new_sn_is_epoch_milliseconds_as_a_string():
    sn = protocol.new_sn()

    assert sn.isdigit()
    assert len(sn) == 13


def test_successive_frames_do_not_reuse_a_sequence_number():
    # Responses are correlated by sn; a collision would let one command's
    # reply satisfy another's wait.
    assert protocol.new_sn() != protocol.new_sn()
