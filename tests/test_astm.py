"""Tests for the ASTM E1381 codec and session layer."""

import asyncio
import socket

from mllp_gateway.astm import codec
from mllp_gateway.astm.session import ASTMSession


def test_frame_round_trip():
    record = r"H|\^&|||Analyzer|||||||P|E1394-97|20240101120000"
    frame = codec.build_frame(record, 1, last=True)
    frame_number, text, is_last, checksum_ok = codec.parse_frame(frame)
    assert text == record
    assert frame_number == 1
    assert is_last is True
    assert checksum_ok is True


def test_checksum_is_uppercase_hex():
    body = b"1" + b"P|1" + codec.ETX
    assert codec.make_checksum(body) == b"31"


def test_parse_frame_detects_bad_checksum():
    frame = codec.build_frame("R|1|^^^WBC|7.5", 2, last=True)
    corrupted = frame[:-3] + b"00" + codec.CR  # overwrite checksum
    _fn, _text, _last, checksum_ok = codec.parse_frame(corrupted)
    assert checksum_ok is False


def test_intermediate_frame_uses_etb():
    frame = codec.build_frame("partial", 3, last=False)
    assert codec.ETB in frame
    _fn, _text, is_last, ok = codec.parse_frame(frame)
    assert is_last is False
    assert ok is True


def test_detect_delimiters_from_header():
    assert codec.detect_delimiters(r"H|\^&|||X") == ("|", "\\", "^", "&")
    # Non-standard field delimiter
    assert codec.detect_delimiters("H#\\^&#")[0] == "#"


def test_record_type():
    assert codec.record_type("P|1|x") == "P"
    assert codec.record_type("") == ""


async def _connected_pair():
    s1, s2 = socket.socketpair()
    r1, w1 = await asyncio.open_connection(sock=s1)
    r2, w2 = await asyncio.open_connection(sock=s2)
    return (r1, w1), (r2, w2)


async def test_session_round_trip():
    (r1, w1), (r2, w2) = await _connected_pair()
    sender = ASTMSession(r1, w1, "sender")
    receiver = ASTMSession(r2, w2, "receiver")
    records = [
        r"H|\^&|||Analyzer|||||||P||E1394-97|20240101",
        "P|1||PID123||Doe^John||19800101|M",
        "O|1|SAMPLE001||^^^CBC|R||20240101120000",
        "R|1|^^^WBC|7.5|10*9/L|4.0-10.0|N||F",
        "L|1|N",
    ]

    async def receive():
        token = await receiver.wait_for_establishment(timeout=5)
        assert token == codec.ENQ
        return await receiver.receive_message()

    recv_task = asyncio.create_task(receive())
    ok = await sender.send_message(records)
    got = await recv_task
    assert ok is True
    assert got == records
    w1.close()
    w2.close()
