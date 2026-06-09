"""Serial (RS232) transport helpers.

``serial_asyncio`` gives us plain :class:`asyncio.StreamReader` /
:class:`asyncio.StreamWriter` pairs over a serial port. For HL7 we layer
MLLP framing via :class:`MllpConnection`; for ASTM we use the raw byte
streams directly and let the ASTM framing layer handle E1381 control
characters.
"""

from __future__ import annotations

import asyncio
import logging

import serial_asyncio

from mllp_gateway.mllp.framing import MllpConnection
from mllp_gateway.transport.device import SerialSettings

logger = logging.getLogger(__name__)


async def open_hl7_serial_connection(
    settings: SerialSettings,
) -> MllpConnection:
    """Open an MLLP-framed HL7 connection over a serial port.

    Returns an :class:`MllpConnection` so the ORU/ORM session handlers work
    identically over serial and TCP.
    """
    reader, writer = await serial_asyncio.open_serial_connection(
        **settings.to_pyserial_kwargs()
    )
    logger.info("Opened MLLP serial connection on %s", settings.port)
    return MllpConnection(reader, writer)


async def open_serial_stream(
    settings: SerialSettings,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a raw byte stream over a serial port (used by the ASTM framing layer)."""
    reader, writer = await serial_asyncio.open_serial_connection(
        **settings.to_pyserial_kwargs()
    )
    logger.info("Opened raw serial stream on %s", settings.port)
    return reader, writer
