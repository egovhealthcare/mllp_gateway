"""Inbound ASTM E1381 TCP server for analyzers that connect to the gateway."""

from __future__ import annotations

import asyncio
import logging

from mllp_gateway.astm import codec as astm_codec
from mllp_gateway.astm.session import ASTMSession
from mllp_gateway.care import CareClient
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.astm.handler import handle_astm_message
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp.framing import MllpConnection
from mllp_gateway.transport.device import DeviceConfig

logger = logging.getLogger(__name__)


async def _serve_inbound_astm_connection(
    device: DeviceConfig,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
) -> None:
    """Handle one inbound ASTM TCP connection until the analyzer closes it."""
    peer = writer.get_extra_info("peername")
    peer_ip = peer[0] if peer else device.connection_key
    session = ASTMSession(reader, writer, peer_ip)
    conn = MllpConnection(reader, writer)
    connections.register_oru(device.id, conn)
    try:
        while True:
            token = await session.wait_for_establishment(timeout=None)
            if token is None:
                return
            if token != astm_codec.ENQ:
                continue
            connections.record_activity(device.id)
            records = await session.receive_message()
            if not records:
                continue
            await handle_astm_message(device, records, session, care, store)
    finally:
        connections.unregister_oru(device.id)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_inbound_astm_listener(
    device: DeviceConfig,
    host: str,
    port: int,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    stop_event: asyncio.Event,
) -> None:
    """Listen for inbound ASTM connections from a single configured device."""

    async def on_connect(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await _serve_inbound_astm_connection(
                device, reader, writer, connections, care, store
            )
        except Exception as e:  # noqa: BLE001 — keep listener alive
            logger.warning(
                "Inbound ASTM connection error for device %s: %s", device.id, e
            )

    server = await asyncio.start_server(on_connect, host, port)
    logger.info(
        "ASTM inbound listening on %s:%d for device %s (%s)",
        host,
        port,
        device.registered_name or device.id,
        device.type,
    )
    async with server:
        await stop_event.wait()
