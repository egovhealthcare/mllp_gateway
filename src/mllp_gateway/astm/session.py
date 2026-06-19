"""ASTM E1381 session layer: establishment, frame transfer, termination.

Implements the low-level handshake over any asyncio byte stream (TCP or
serial):

- **Establishment** — sender transmits ``ENQ``, receiver replies ``ACK``.
- **Transfer** — each record is sent as a numbered frame and individually
  acknowledged (``ACK``); a bad checksum yields ``NAK`` and a retransmit.
- **Termination** — sender transmits ``EOT``.

The gateway acts as receiver for results coming from an analyzer and as
sender when delivering orders. A single :class:`ASTMSession` instance handles
both directions over the same link.
"""

from __future__ import annotations

import asyncio
import logging

from mllp_gateway.astm import codec
from mllp_gateway.astm.codec import (
    ACK,
    CR,
    ENQ,
    EOT,
    NAK,
    STX,
)

logger = logging.getLogger(__name__)

# Timeouts (seconds) per ASTM E1381 guidance.
RECEIVE_TIMEOUT = 30.0  # waiting for a frame after ACKing
REPLY_TIMEOUT = 15.0  # waiting for ACK/NAK after sending a frame
MAX_NAK_RETRIES = 6
MAX_FRAME_BYTES = 64 * 1024


class ASTMSession:
    """Bidirectional ASTM E1381 session over an asyncio stream pair."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_id: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.peer_id = peer_id
        self._recv_buffer = b""

    # -- low-level IO --------------------------------------------------

    async def _read_byte(self, timeout: float) -> bytes:
        if self._recv_buffer:
            byte = self._recv_buffer[:1]
            self._recv_buffer = self._recv_buffer[1:]
            return byte
        return await asyncio.wait_for(self._reader.readexactly(1), timeout)

    async def _read_token(self, timeout: float) -> tuple[str, bytes]:
        """Read the next control byte or full frame.

        Returns ``("control", byte)`` for ENQ/ACK/NAK/EOT, or
        ``("frame", raw_frame)`` for an ``STX``…``CR`` frame. Stray CR/LF/NUL
        bytes between tokens are skipped.
        """
        while True:
            byte = await self._read_byte(timeout)
            if byte in (codec.CR, codec.LF, b"\x00"):
                continue
            if byte in (ENQ, ACK, NAK, EOT):
                return "control", byte
            if byte == STX:
                return "frame", await self._read_frame_after_stx(timeout)
            # Unexpected byte — log and keep scanning.
            logger.debug("[%s] skipping unexpected ASTM byte %r", self.peer_id, byte)

    async def _send(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def _read_frame_after_stx(self, timeout: float) -> bytes:
        """Read a complete ASTM frame after the leading ``STX`` byte."""
        buffer = STX
        while True:
            extracted = codec.try_extract_frame(buffer)
            if extracted is not None:
                frame, remainder = extracted
                self._recv_buffer = remainder + self._recv_buffer
                return frame
            if len(buffer) >= MAX_FRAME_BYTES:
                raise ValueError("ASTM frame exceeds maximum size")
            chunk = await asyncio.wait_for(self._reader.read(1), timeout)
            if not chunk:
                raise asyncio.IncompleteReadError(buffer, len(buffer) + 1)
            buffer += chunk

    # -- receiving (gateway as receiver) -------------------------------

    async def receive_message(self) -> list[str] | None:
        """Receive one complete ASTM message after an ``ENQ``.

        Assumes the establishment ``ENQ`` has just been read. ACKs it, then
        receives and acknowledges frames until ``EOT``. Returns the list of
        record lines, or ``None`` if the peer aborted before sending data.

        Analyzers such as the Sysmex XP-300 require an ``ACK`` immediately on
        each frame before any host processing; they do not tolerate ``NAK``.
        """
        await self._send(ACK)
        records: list[str] = []
        partial = ""
        pending = self._recv_buffer
        self._recv_buffer = b""

        while True:
            if not pending:
                try:
                    chunk = await asyncio.wait_for(
                        self._reader.read(4096), RECEIVE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("[%s] ASTM receive timed out", self.peer_id)
                    return records or None
                if not chunk:
                    return records or None
                pending += chunk

            if pending.startswith(ENQ):
                await self._send(ACK)
                pending = pending[1:]
                continue

            if pending.startswith(EOT):
                if partial:
                    records.append(partial)
                return records or None

            if pending[:1] in (codec.CR, codec.LF, b"\x00"):
                pending = pending[1:]
                continue

            if not pending.startswith(STX):
                logger.debug(
                    "[%s] skipping unexpected ASTM byte %r",
                    self.peer_id,
                    pending[:1],
                )
                pending = pending[1:]
                continue

            extracted = codec.try_extract_frame(pending)
            if extracted is None:
                if len(pending) >= MAX_FRAME_BYTES:
                    logger.warning("[%s] ASTM frame exceeds maximum size", self.peer_id)
                    pending = pending[1:]
                    continue
                continue

            frame, pending = extracted
            await self._send(ACK)

            try:
                _frame_number, text, is_last, checksum_ok = codec.parse_frame(frame)
            except ValueError as exc:
                logger.warning("[%s] ASTM frame parse error: %s", self.peer_id, exc)
                continue

            if not checksum_ok:
                logger.warning("[%s] ASTM frame checksum mismatch", self.peer_id)

            partial += text
            if is_last:
                records.append(partial)
                partial = ""

    # -- sending (gateway as sender) -----------------------------------

    async def send_message(self, records: list[str]) -> bool:
        """Send a complete ASTM message as the establishing sender.

        Returns True if the peer acknowledged termination cleanly.
        """
        # Establishment
        await self._send(ENQ)
        try:
            kind, token = await self._read_token(REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("[%s] no response to ASTM ENQ", self.peer_id)
            return False
        if kind != "control" or token != ACK:
            logger.error("[%s] ASTM establishment rejected (%r)", self.peer_id, token)
            await self._send(EOT)
            return False

        # Transfer — one frame per record, each individually acknowledged.
        for index, record in enumerate(records, start=1):
            frame = codec.build_frame(record, index, last=True)
            if not await self._send_frame_with_retry(frame):
                await self._send(EOT)
                return False

        # Termination
        await self._send(EOT)
        return True

    async def _send_frame_with_retry(self, frame: bytes) -> bool:
        for attempt in range(1, MAX_NAK_RETRIES + 1):
            await self._send(frame)
            try:
                kind, token = await self._read_token(REPLY_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "[%s] ASTM frame ACK timeout (attempt %d)", self.peer_id, attempt
                )
                continue
            if kind == "control" and token == ACK:
                return True
            if kind == "control" and token == NAK:
                logger.warning(
                    "[%s] ASTM frame NAK (attempt %d) — retransmitting",
                    self.peer_id,
                    attempt,
                )
                continue
            logger.warning("[%s] unexpected ASTM reply %r", self.peer_id, token)
        return False

    # -- receiver loop -------------------------------------------------

    async def wait_for_establishment(self, timeout: float | None = None) -> bytes | None:
        """Block until the peer sends ``ENQ`` (or another control token).

        Returns the control byte read, or ``None`` on EOF/timeout. Frames
        received without establishment are ignored.
        """
        while True:
            try:
                kind, token = await self._read_token(
                    timeout if timeout is not None else RECEIVE_TIMEOUT
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return None
            if kind == "control":
                return token
            # Stray frame before ENQ — ACK so strict analyzers do not time out.
            await self._send(ACK)
