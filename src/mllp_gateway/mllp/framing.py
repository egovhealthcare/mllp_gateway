"""Unified MLLP framing over any asyncio stream (TCP, serial, or outbound dial-out).

All HL7/MLLP read and write operations go through :class:`MllpConnection`.
The read path uses ``readuntil(VT)`` to sync on the MLLP start-block, which
transparently skips ENQ keep-alives, heartbeat bytes, and other pre-frame
noise sent by devices like the Mindray BC-5150 and ADX-HEME-340.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import hl7

from mllp_gateway.transport.device import SerialSettings

logger = logging.getLogger(__name__)

VT = b"\x0b"
FS_CR = b"\x1c\x0d"


def mllp_encode(message: str | hl7.Message) -> bytes:
    """Wrap an HL7 message in MLLP framing: <VT> message <FS><CR>."""
    return VT + str(message).encode("utf-8") + FS_CR


class MllpConnection:
    """Unified MLLP read/write over any asyncio stream pair.

    Wraps a plain :class:`asyncio.StreamReader` / :class:`asyncio.StreamWriter`
    with MLLP framing. The read path syncs on ``<VT>`` and tolerates pre-frame
    garbage (ENQ, heartbeats, stray bytes).
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        encoding: str = "utf-8",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._encoding = encoding

    @property
    def peer(self) -> str:
        peername = self._writer.get_extra_info("peername")
        return peername[0] if peername else "unknown"

    @property
    def at_eof(self) -> bool:
        return self._reader.at_eof()

    async def read_message(self) -> hl7.Message:
        """Read one MLLP-framed HL7 message.

        ``readuntil(VT)`` consumes any bytes before the start-block (ENQ
        keep-alives, heartbeats, etc.) without responding to them, then reads
        the payload through ``<FS><CR>``.

        Raises :class:`asyncio.IncompleteReadError` on EOF and
        :class:`asyncio.LimitOverrunError` on oversized frames.
        """
        await self._reader.readuntil(VT)
        payload_with_end = await self._reader.readuntil(FS_CR)
        hl7_text = payload_with_end[:-2].decode(self._encoding, errors="replace")
        return hl7.parse(hl7_text)

    def write_message(self, message: hl7.Message | str) -> None:
        """Buffer an MLLP-framed HL7 message for sending (call drain after)."""
        self._writer.write(mllp_encode(message))

    async def drain(self) -> None:
        await self._writer.drain()

    async def send_message(self, message: hl7.Message | str) -> None:
        """Write and flush an MLLP-framed HL7 message."""
        self.write_message(message)
        await self.drain()

    def close(self) -> None:
        self._writer.close()

    async def wait_closed(self) -> None:
        await self._writer.wait_closed()

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._writer.get_extra_info(name, default)


async def open_connection(
    host: str,
    port: int,
    *,
    timeout: float = 10,
) -> MllpConnection:
    """Open an outbound TCP MLLP connection (e.g. BC-5150:5100 or ORM client)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise ConnectionError(
            f"Could not connect to {host}:{port} within {timeout}s. "
            "Verify the analyzer is powered on and listening."
        ) from exc
    except OSError as exc:
        raise ConnectionError(
            f"Connection refused by {host}:{port}: {exc}. "
            "Verify the analyzer IP and port are correct."
        ) from exc
    return MllpConnection(reader, writer)


async def start_server(
    handler,
    host: str,
    port: int,
) -> asyncio.Server:
    """Start a TCP server that wraps each connection in :class:`MllpConnection`."""

    async def _on_connect(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = MllpConnection(reader, writer)
        try:
            await handler(conn)
        finally:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass

    return await asyncio.start_server(_on_connect, host, port)


async def open_serial_connection(settings: SerialSettings) -> MllpConnection:
    """Open an MLLP connection over a serial port."""
    import serial_asyncio

    reader, writer = await serial_asyncio.open_serial_connection(
        **settings.to_pyserial_kwargs()
    )
    logger.info("Opened MLLP serial connection on %s", settings.port)
    return MllpConnection(reader, writer)
