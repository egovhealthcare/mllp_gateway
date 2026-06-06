"""Per-device runners for serial and ASTM transports.

Ethernet + HL7 analyzers are served by the shared MLLP TCP listeners (they
connect *to* the gateway). The remaining combinations need a dedicated
long-running task per device:

- **serial + hl7** — the gateway opens the serial port and runs the standard
  ORU session over an MLLP-framed serial link.
- **serial + astm** — the gateway opens the serial port and runs an ASTM
  E1381 session.
- **ethernet + astm** — the gateway connects out to the analyzer's TCP port
  and runs an ASTM E1381 session.

Every runner reconnects with backoff until the stop event is set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from functools import partial

from mllp_gateway.astm import ASTMSession
from mllp_gateway.astm import codec as astm_codec
from mllp_gateway.care import CareClient
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import serve_oru_connection
from mllp_gateway.mllp.server import WorklistHandler
from mllp_gateway.transport.device import DeviceConfig
from mllp_gateway.transport.serial import (
    open_hl7_serial_connection,
    open_serial_stream,
)

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_BASE = 2  # seconds
_RECONNECT_BACKOFF_MAX = 60


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

    Ethernet + HL7 devices are skipped: they are handled by the shared MLLP
    TCP listeners. Returns the list of created tasks.
    """
    tasks: list[asyncio.Task] = []
    for device in devices:
        if device.transport == "ethernet" and device.protocol == "hl7":
            continue  # served by the shared MLLP TCP listeners
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
            if device.is_serial and not device.is_astm:
                await _run_serial_hl7(
                    device, connections, care, store, worklist_handler, stop_event
                )
            elif device.is_astm:
                await _run_astm(device, connections, care, store, stop_event)
            else:  # pragma: no cover — ethernet+hl7 is filtered out earlier
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
    reader, writer = await open_hl7_serial_connection(device.serial)
    # Tag forwarded results with the device id so CARE can resolve a device
    # that has no IP address.
    forward = partial(_forward_with_device_id, care, device.id)
    await serve_oru_connection(
        reader,
        writer,
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
    connections.register_oru(device.connection_key, reader, writer)
    try:
        while not stop_event.is_set():
            token = await session.wait_for_establishment(timeout=None)
            if token is None:
                return  # link closed
            if token != astm_codec.ENQ:
                continue
            connections.record_activity(device.connection_key)
            records = await session.receive_message()
            if not records:
                continue
            await _handle_astm_message(device, records, session, care, store)
    finally:
        connections.unregister_oru(device.connection_key)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _is_astm_query(records: list[str]) -> bool:
    """True if the message is a host-query request (contains a ``Q`` record)."""
    return any(astm_codec.record_type(line) == "Q" for line in records)


def _extract_astm_sample_ids(records: list[str]) -> list[str]:
    """Pull sample/specimen IDs from ``Q`` query records.

    ASTM Q records carry the requested sample range in field 3
    (``starting range ID``), e.g. ``Q|1|^SAMPLE001||ALL||...``.
    """
    sample_ids: list[str] = []
    for line in records:
        if astm_codec.record_type(line) != "Q":
            continue
        fields = astm_codec.split_fields(line)
        if len(fields) > 2 and fields[2]:
            # Strip leading component delimiters (e.g. "^SAMPLE001").
            sample_ids.append(fields[2].lstrip("^").split("^")[0])
    return [s for s in sample_ids if s]


async def _handle_astm_message(
    device: DeviceConfig,
    records: list[str],
    session: ASTMSession,
    care: CareClient,
    store: MessageStore,
) -> None:
    raw = "\n".join(records)
    now = datetime.now(timezone.utc).isoformat()

    if _is_astm_query(records):
        sample_ids = _extract_astm_sample_ids(records)
        await store.insert(
            "received",
            message=raw,
            ack="",
            peer=device.connection_key,
            time=now,
            forwarded=1,
        )
        try:
            result = await care.fetch_pending_orders(
                device.connection_key,
                sample_ids,
                raw_message=raw,
                sender_device_id=device.id,
            )
        except Exception as e:
            logger.error("ASTM pending-orders fetch failed for %s: %s", device.id, e)
            return
        response_text = result.get("raw_astm_response") or result.get("raw_response")
        if response_text:
            order_records = [
                line for line in response_text.replace("\r", "\n").split("\n") if line
            ]
            ok = await session.send_message(order_records)
            logger.info(
                "[DEVICE -->] Sent ASTM worklist (%d records) to %s (ok=%s)",
                len(order_records),
                device.connection_key,
                ok,
            )
        return

    # Result message — store and forward to CARE.
    row = await store.insert(
        "received",
        message=raw,
        ack="",
        peer=device.connection_key,
        time=now,
    )
    try:
        await care.forward_result(
            raw, device.connection_key, sender_device_id=device.id
        )
        await store.update_forward_status(row["id"], True)
        logger.info("Forwarded ASTM result from %s", device.connection_key)
    except Exception as e:
        logger.error(
            "Failed to forward ASTM result from %s: %s (will retry)",
            device.connection_key,
            e,
        )
