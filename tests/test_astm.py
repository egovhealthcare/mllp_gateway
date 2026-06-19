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


def build_xp300_style_frame(record: str, frame_number: int) -> bytes:
    """XP-300 embeds CR before ETX inside the frame body."""
    fn = str(frame_number % codec.FRAME_NUMBER_MODULO).encode("ascii")
    body = fn + record.encode("ascii") + codec.CR + codec.ETX
    checksum = codec.make_checksum(body)
    return codec.STX + body + checksum + codec.CRLF


def test_parse_xp300_style_frame_with_embedded_cr():
    record = r"H|\^&|||XP-300^00-16^^^^C2524^AK007119||||||||E1394-97"
    frame = build_xp300_style_frame(record, 1)
    extracted, remainder = codec.try_extract_frame(frame)
    assert extracted == frame
    assert remainder == b""
    frame_number, text, is_last, checksum_ok = codec.parse_frame(frame)
    assert frame_number == 1
    assert text == record
    assert is_last is True
    assert checksum_ok is True


def test_try_extract_frame_leaves_remainder():
    frame = build_xp300_style_frame("P|1", 2)
    extracted, remainder = codec.try_extract_frame(frame + codec.ENQ)
    assert extracted == frame
    assert remainder == codec.ENQ


def test_try_extract_frame_back_to_back_without_crlf():
    frame_a = build_xp300_style_frame("P|1", 1)[:-2]  # drop CR LF
    frame_b = build_xp300_style_frame("L|1|N", 2)
    buffer = frame_a + frame_b
    extracted, remainder = codec.try_extract_frame(buffer)
    assert extracted == frame_a
    assert remainder == frame_b


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


async def test_session_receives_xp300_style_frames():
    (r1, w1), (r2, w2) = await _connected_pair()
    receiver = ASTMSession(r2, w2, "receiver")
    records = [
        r"H|\^&|||XP-300^00-16|||||||E1394-97",
        "P|1",
        "L|1|N",
    ]

    async def receive():
        token = await receiver.wait_for_establishment(timeout=5)
        assert token == codec.ENQ
        return await receiver.receive_message()

    recv_task = asyncio.create_task(receive())
    w1.write(codec.ENQ)
    await w1.drain()
    ack = await r1.readexactly(1)
    assert ack == codec.ACK
    for index, record in enumerate(records, start=1):
        frame = build_xp300_style_frame(record, index)
        w1.write(frame)
        await w1.drain()
        ack = await r1.readexactly(1)
        assert ack == codec.ACK
    w1.write(codec.EOT)
    await w1.drain()

    got = await recv_task
    assert got == records
    w1.close()
    w2.close()


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
