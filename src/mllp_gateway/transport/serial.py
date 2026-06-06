"""Serial (RS232) transport helpers.

``serial_asyncio`` gives us plain :class:`asyncio.StreamReader` /
:class:`asyncio.StreamWriter` pairs over a serial port. For HL7 we layer the
``hl7.mllp`` MLLP framing on top (mirroring ``hl7.mllp.open_hl7_connection``
but for a serial transport); for ASTM we use the raw byte streams directly and
let the ASTM framing layer handle E1381 control characters.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import get_event_loop

import serial_asyncio
from hl7.mllp.streams import HL7StreamProtocol, HL7StreamReader, HL7StreamWriter

from mllp_gateway.transport.device import SerialSettings

logger = logging.getLogger(__name__)

# Matches asyncio.streams._DEFAULT_LIMIT (64 KiB) without importing a private name.
_STREAM_LIMIT = 2**16


async def open_hl7_serial_connection(
    settings: SerialSettings,
) -> tuple[HL7StreamReader, HL7StreamWriter]:
    """Open an MLLP-framed HL7 connection over a serial port.

    Returns the same ``(HL7StreamReader, HL7StreamWriter)`` pair that the rest
    of the gateway already uses for TCP, so the ORU/ORM session handlers work
    unchanged over serial.
    """
    loop = get_event_loop()
    reader = HL7StreamReader(limit=_STREAM_LIMIT, loop=loop)
    protocol = HL7StreamProtocol(reader, loop=loop)
    transport, _ = await serial_asyncio.create_serial_connection(
        loop, lambda: protocol, **settings.to_pyserial_kwargs()
    )
    writer = HL7StreamWriter(transport, protocol, reader, loop)
    logger.info("Opened HL7 serial connection on %s", settings.port)
    return reader, writer


async def open_serial_stream(
    settings: SerialSettings,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a raw byte stream over a serial port (used by the ASTM framing layer)."""
    reader, writer = await serial_asyncio.open_serial_connection(
        **settings.to_pyserial_kwargs()
    )
    logger.info("Opened raw serial stream on %s", settings.port)
    return reader, writer
