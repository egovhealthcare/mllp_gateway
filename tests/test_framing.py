"""Tests for MLLP framing (MllpConnection read/write)."""

import asyncio
import unittest

from mllp_gateway.mllp.framing import FS_CR, VT, MllpConnection, mllp_encode

_SAMPLE = (
    "MSH|^~\\&|DEV|||20260101120000||ORU^R01|1|P|2.3.1\r"
    "PID|1||P1\r"
    "OBR|1||S1\r"
    "OBX|1|NM|6690-2^WBC^LN||7.5|10*9/L|||N|||F"
)


class TestMllpEncode(unittest.TestCase):

    def test_framing_boundaries(self) -> None:
        framed = mllp_encode(_SAMPLE)
        self.assertTrue(framed.startswith(VT))
        self.assertTrue(framed.endswith(FS_CR))

    def test_payload_preserved(self) -> None:
        framed = mllp_encode(_SAMPLE)
        payload = framed[len(VT) : -len(FS_CR)]
        self.assertEqual(payload.decode("utf-8"), _SAMPLE)


class TestMllpConnectionRead(unittest.TestCase):

    def test_reads_plain_mllp(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader()
            reader.feed_data(mllp_encode(_SAMPLE))
            reader.feed_eof()
            writer_transport = asyncio.get_event_loop().create_future()
            writer_transport.cancel()

            conn = MllpConnection(reader, _FakeWriter())
            msg = await conn.read_message()
            self.assertIn("ORU^R01", str(msg))

        asyncio.run(run())

    def test_skips_enq_before_vt(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader()
            reader.feed_data(b"\x05\x05" + mllp_encode(_SAMPLE))
            reader.feed_eof()

            conn = MllpConnection(reader, _FakeWriter())
            msg = await conn.read_message()
            self.assertIn("ORU^R01", str(msg))

        asyncio.run(run())

    def test_skips_garbage_before_vt(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader()
            reader.feed_data(b"\xff\xfe" + mllp_encode(_SAMPLE))
            reader.feed_eof()

            conn = MllpConnection(reader, _FakeWriter())
            msg = await conn.read_message()
            self.assertIn("ORU^R01", str(msg))

        asyncio.run(run())

    def test_reads_multiple_messages(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader()
            reader.feed_data(mllp_encode(_SAMPLE) + mllp_encode(_SAMPLE))
            reader.feed_eof()

            conn = MllpConnection(reader, _FakeWriter())
            msg1 = await conn.read_message()
            msg2 = await conn.read_message()
            self.assertIn("ORU^R01", str(msg1))
            self.assertIn("ORU^R01", str(msg2))

        asyncio.run(run())


class TestMllpConnectionWrite(unittest.TestCase):

    def test_write_message_produces_framed_output(self) -> None:
        async def run() -> None:
            fake = _FakeWriter()
            conn = MllpConnection(asyncio.StreamReader(), fake)
            conn.write_message(_SAMPLE)
            await conn.drain()
            self.assertTrue(fake.data.startswith(VT))
            self.assertTrue(fake.data.endswith(FS_CR))

        asyncio.run(run())


class _FakeWriter:
    """Minimal stand-in for asyncio.StreamWriter in tests."""

    def __init__(self) -> None:
        self.data = b""
        self._closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    async def wait_closed(self) -> None:
        pass

    def get_extra_info(self, name: str, default=None):
        if name == "peername":
            return ("127.0.0.1", 12345)
        return default


if __name__ == "__main__":
    unittest.main()
