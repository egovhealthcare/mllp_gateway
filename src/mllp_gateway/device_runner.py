"""Per-device runners for serial and ASTM transports.

Ethernet + HL7 analyzers are served by the shared MLLP TCP listeners (they
connect *to* the gateway). The remaining combinations need a dedicated
long-running task per device:

- **serial + hl7** — the gateway opens the serial port and runs the standard
  ORU session over an MLLP-framed serial link.
- **serial + astm** — the gateway opens the serial port and runs an ASTM
  E1381 session.
- **ethernet + astm (outbound)** — the gateway connects out to the analyzer's
  TCP port and runs an ASTM E1381 session.
- **ethernet + astm (inbound)** — served by a dedicated ASTM TCP listener
  (e.g. Sysmex XP-300 on port 5006).

Every runner reconnects with backoff until the stop event is set.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from mllp_gateway.astm import ASTMSession
from mllp_gateway.astm import codec as astm_codec
from mllp_gateway.astm.handler import handle_astm_message
from mllp_gateway.care import CareClient
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import run_outbound_hl7_device, serve_oru_connection
from mllp_gateway.mllp.framing import MllpConnection
from mllp_gateway.mllp.server import WorklistHandler
from mllp_gateway.transport.device import DeviceConfig
from mllp_gateway.transport.serial import (
    open_hl7_serial_connection,
    open_serial_stream,
)

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_BASE = 10  # seconds
_RECONNECT_BACKOFF_MAX = 120


async def _sleep_or_stop(stop_event: asyncio.Event, delay: float) -> bool:
    """Sleep up to *delay* seconds. Returns True if the stop event fired."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False


def run_configured_devices(
    devices: list[DeviceConfig],
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    worklist_handler: WorklistHandler | None,
    stop_event: asyncio.Event,
) -> list[asyncio.Task]:
    """Spawn a runner task for each device that needs a dedicated link.

    Inbound Ethernet + HL7 devices are skipped — they connect to the shared
    MLLP TCP listeners. Outbound Ethernet + HL7 devices (e.g. Mindray BC-5150)
    get a dedicated dial-out runner. Returns the list of created tasks.
    """
    tasks: list[asyncio.Task] = []
    for device in devices:
        if device.transport == "ethernet" and device.protocol == "hl7" and not device.is_outbound_hl7:
            continue  # served by the shared MLLP TCP listeners
        if device.is_inbound_astm:
            continue  # served by dedicated inbound ASTM listeners
        tasks.append(
            asyncio.create_task(
                _run_device(
                    device, connections, care, store, worklist_handler, stop_event
                ),
                name=f"device-{device.connection_key}",
            )
        )
        logger.info(
            "Starting runner for device %s (%s/%s)",
            device.registered_name or device.id,
            device.transport,
            device.protocol,
        )
    return tasks


async def _run_device(
    device: DeviceConfig,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    worklist_handler: WorklistHandler | None,
    stop_event: asyncio.Event,
) -> None:
    attempt = 0
    while not stop_event.is_set():
        try:
            if device.is_outbound_hl7:
                await _run_outbound_hl7(
                    device, connections, care, store, worklist_handler, stop_event
                )
            elif device.is_serial and not device.is_astm:
                await _run_serial_hl7(
                    device, connections, care, store, worklist_handler, stop_event
                )
            elif device.is_astm:
                await _run_astm(device, connections, care, store, stop_event)
            else:  # pragma: no cover — inbound ethernet+hl7 is filtered out earlier
                return
            attempt = 0  # clean exit (link closed) — reset backoff
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — keep the runner alive across link errors
            logger.warning(
                "Device %s link error: %s", device.connection_key, e
            )
        if stop_event.is_set():
            return
        attempt += 1
        delay = min(_RECONNECT_BACKOFF_BASE * attempt, _RECONNECT_BACKOFF_MAX)
        logger.info(
            "Reconnecting to device %s in %ds", device.connection_key, delay
        )
        if await _sleep_or_stop(stop_event, delay):
            return


async def _run_outbound_hl7(
    device: DeviceConfig,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    worklist_handler: WorklistHandler | None,
    stop_event: asyncio.Event,
) -> None:
    """Dial out to an analyzer that listens as an MLLP server (e.g. BC-5150:5100)."""
    if not device.endpoint_address:
        logger.error(
            "Outbound HL7 device %s has no endpoint_address", device.id
        )
        await _sleep_or_stop(stop_event, _RECONNECT_BACKOFF_MAX)
        return

    async def forward(raw_message: str, peer: str, sender_device_id: str) -> None:
        await care.forward_result(raw_message, peer, sender_device_id=sender_device_id)

    await run_outbound_hl7_device(
        device_id=device.id,
        host=device.endpoint_address,
        port=device.oru_port,
        peer_id=device.connection_key,
        connections=connections,
        forward=forward,
        store=store,
        worklist_handler=worklist_handler,
        stop_event=stop_event,
        reconnect_backoff_base=_RECONNECT_BACKOFF_BASE,
        reconnect_backoff_max=_RECONNECT_BACKOFF_MAX,
    )


async def _run_serial_hl7(
    device: DeviceConfig,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    worklist_handler: WorklistHandler | None,
    stop_event: asyncio.Event,
) -> None:
    """Open an MLLP-framed HL7 serial link and serve it via the ORU handler."""
    assert device.serial is not None
    conn = await open_hl7_serial_connection(device.serial)
    forward = partial(_forward_with_device_id, care, device.id)
    await serve_oru_connection(
        conn,
        device.connection_key,
        connections,
        forward,
        store,
        worklist_handler=worklist_handler,
    )


async def _forward_with_device_id(
    care: CareClient, device_id: str, raw_message: str, peer: str
) -> None:
    await care.forward_result(raw_message, peer, sender_device_id=device_id)


async def _run_astm(
    device: DeviceConfig,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    stop_event: asyncio.Event,
) -> None:
    """Open an ASTM link (serial or TCP) and process inbound messages."""
    if device.is_serial:
        assert device.serial is not None
        reader, writer = await open_serial_stream(device.serial)
    else:
        if not device.endpoint_address:
            logger.error(
                "ASTM/ethernet device %s has no endpoint_address", device.id
            )
            await _sleep_or_stop(stop_event, _RECONNECT_BACKOFF_MAX)
            return
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(device.endpoint_address, device.oru_port),
            timeout=10,
        )

    session = ASTMSession(reader, writer, device.connection_key)
    conn = MllpConnection(reader, writer)
    connections.register_oru(device.connection_key, conn)
    try:
        await serve_astm_session(
            device, session, connections, care, store, stop_event
        )
    finally:
        connections.unregister_oru(device.connection_key)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def serve_astm_session(
    device: DeviceConfig,
    session: ASTMSession,
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    stop_event: asyncio.Event,
) -> None:
    """Process ASTM messages on an established link until closed or stopped."""
    while not stop_event.is_set():
        token = await session.wait_for_establishment(timeout=None)
        if token is None:
            return
        if token != astm_codec.ENQ:
            continue
        connections.record_activity(device.connection_key)
        records = await session.receive_message()
        if not records:
            continue
        await handle_astm_message(device, records, session, care, store)


def run_inbound_astm_servers(
    devices: list[DeviceConfig],
    connections: ConnectionManager,
    care: CareClient,
    store: MessageStore,
    stop_event: asyncio.Event,
) -> list[asyncio.Task]:
    """Spawn inbound ASTM listener tasks for configured devices."""
    from mllp_gateway.astm.server import run_inbound_astm_listener

    tasks: list[asyncio.Task] = []
    for device in devices:
        if not device.is_inbound_astm:
            continue
        tasks.append(
            asyncio.create_task(
                run_inbound_astm_listener(
                    device,
                    "0.0.0.0",
                    device.oru_port,
                    connections,
                    care,
                    store,
                    stop_event,
                ),
                name=f"astm-inbound-{device.id}",
            )
        )
        logger.info(
            "Starting inbound ASTM listener for %s on port %d",
            device.registered_name or device.id,
            device.oru_port,
        )
    return tasks


