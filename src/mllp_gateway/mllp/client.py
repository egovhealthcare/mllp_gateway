"""ORM dispatch and outbound MLLP client connections to lab analyzers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import hl7

from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp.common import validate_hl7
from mllp_gateway.mllp.framing import MllpConnection, open_connection
from mllp_gateway.mllp.server import ForwardCallback, WorklistHandler, serve_oru_connection

logger = logging.getLogger(__name__)

ORM_MODES = frozenset({"shared", "server", "client"})

DEFAULT_ORM_TIMEOUT = 30

DEFAULT_CONNECT_TIMEOUT = 10


async def serve_outbound_hl7_connection(
    host: str,
    port: int,
    peer_id: str,
    connections: ConnectionManager,
    forward: ForwardCallback,
    store: MessageStore,
    worklist_handler: WorklistHandler | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> None:
    """Dial an analyzer and serve its ORU/worklist traffic until disconnect.

    Used for devices like the Mindray BC-5150 that listen on a fixed port
    (5100) while the gateway maintains the persistent outbound connection.
    """
    conn = await open_connection(host, port, timeout=connect_timeout)
    logger.info("Outbound HL7 connected to %s:%d", host, port)
    try:
        await serve_oru_connection(
            conn,
            peer_id,
            connections,
            forward,
            store,
            worklist_handler=worklist_handler,
        )
    finally:
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:
            pass


async def run_outbound_hl7_device(
    device_id: str,
    host: str,
    port: int,
    peer_id: str,
    connections: ConnectionManager,
    forward: Callable[[str, str, str], Awaitable[None]],
    store: MessageStore,
    worklist_handler: WorklistHandler | None,
    stop_event: asyncio.Event,
    reconnect_backoff_base: float = 2,
    reconnect_backoff_max: float = 60,
) -> None:
    """Maintain a persistent outbound MLLP session with reconnect backoff."""

    async def forward_with_id(raw_message: str, peer: str) -> None:
        await forward(raw_message, peer, device_id)

    attempt = 0
    while not stop_event.is_set():
        try:
            await serve_outbound_hl7_connection(
                host,
                port,
                peer_id,
                connections,
                forward_with_id,
                store,
                worklist_handler=worklist_handler,
            )
            attempt = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Outbound HL7 link error for %s:%d: %s", host, port, e)
        if stop_event.is_set():
            return
        attempt += 1
        delay = min(reconnect_backoff_base * attempt, reconnect_backoff_max)
        logger.info("Reconnecting outbound HL7 to %s:%d in %ds", host, port, delay)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass


async def send_order(
    connections: ConnectionManager,
    device_ip: str,
    port: int,
    raw_hl7_message: str,
    orm_mode: str = "shared",
    timeout: float = DEFAULT_ORM_TIMEOUT,
) -> str | None:
    """Send an ORM to a device and return the ACK text.

    Delivery strategy depends on *orm_mode*:

    - **shared**: piggyback on the existing ORU connection.
    - **server**: use the dedicated ORM connection the device opened.
    - **client**: open a new TCP connection to ``device_ip:port``.
    """
    if orm_mode not in ORM_MODES:
        raise ValueError(
            f"orm_mode must be one of {', '.join(sorted(ORM_MODES))}, got {orm_mode!r}"
        )

    validation_err = validate_hl7(raw_hl7_message)
    if validation_err:
        raise ValueError(validation_err)

    if orm_mode == "shared":
        conn = connections.get_oru_conn(device_ip)
        lock = connections.get_oru_send_lock(device_ip)
        if conn and lock:
            async with lock:
                fut = asyncio.get_running_loop().create_future()
                connections.set_oru_response_future(device_ip, fut)
                try:
                    await conn.send_message(hl7.parse(raw_hl7_message))
                    return str(await asyncio.wait_for(fut, timeout=timeout))
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"Device {device_ip} did not respond within {timeout}s on shared ORU connection. "
                        "The analyzer may not support receiving orders on this channel — try 'server' or 'client' mode."
                    )
                finally:
                    connections.pop_oru_response_future(device_ip)
        logger.warning(
            "No ORU connection to %s, falling back to client mode", device_ip
        )

    elif orm_mode == "server":
        conn = connections.get_orm_conn(device_ip)
        queue = connections.get_orm_response_queue(device_ip)
        if not (conn and queue):
            raise ConnectionError(
                f"No ORM connection from {device_ip}. "
                "The analyzer must initiate a dedicated ORM connection to use 'server' mode."
            )
        await conn.send_message(hl7.parse(raw_hl7_message))
        try:
            response = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Device {device_ip} did not ACK within {timeout}s on ORM connection. "
                "The analyzer may be busy or unresponsive."
            )
        return str(response)

    # "client" mode or "shared" fallback
    conn = await open_connection(device_ip, port)
    try:
        await conn.send_message(hl7.parse(raw_hl7_message))
        try:
            response = await asyncio.wait_for(conn.read_message(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Device {device_ip}:{port} did not respond within {timeout}s in client mode. "
                "The analyzer may not support receiving orders."
            )
        return str(response)
    finally:
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:
            pass


@dataclass
class OrderResult:
    """Outcome of :func:`dispatch_order`: either ``ok`` with an ACK, or an error."""

    ok: bool
    ack: str | None = None
    error: str | None = None


async def dispatch_order(
    connections: ConnectionManager,
    store: MessageStore,
    device_ip: str,
    port: int,
    raw_message: str,
    orm_mode: str,
    timeout: float = DEFAULT_ORM_TIMEOUT,
) -> OrderResult:
    """Validate, send an ORM to a device, persist the outcome, and return the result."""
    if orm_mode not in ORM_MODES:
        return OrderResult(
            ok=False, error=f"orm_mode must be one of {', '.join(sorted(ORM_MODES))}"
        )

    validation_err = validate_hl7(raw_message)
    if validation_err:
        return OrderResult(ok=False, error=validation_err)

    now = datetime.now(timezone.utc).isoformat()

    logger.info("[DEVICE -->] Sending ORM to %s:%d (mode=%s)", device_ip, port, orm_mode)
    try:
        ack = await send_order(
            connections, device_ip, port, raw_message, orm_mode, timeout
        )
    except (TimeoutError, asyncio.TimeoutError) as e:
        err = str(e) if str(e) else (
            f"Timed out waiting for device response ({orm_mode} mode). "
            "Device may not support receiving on this connection."
        )
        await store.insert(
            "sent",
            message=raw_message,
            status="timeout",
            ack=err,
            host=device_ip,
            port=port,
            time=now,
        )
        return OrderResult(ok=False, error=err)
    except ConnectionError as e:
        err = str(e)
        await store.insert(
            "sent",
            message=raw_message,
            status="connection_error",
            ack=err,
            host=device_ip,
            port=port,
            time=now,
        )
        return OrderResult(ok=False, error=err)
    except ValueError as e:
        return OrderResult(ok=False, error=str(e))
    except Exception as e:
        logger.exception(
            "dispatch_order failed for %s:%d (mode=%s)", device_ip, port, orm_mode
        )
        err = str(e) or type(e).__name__
        await store.insert(
            "sent",
            message=raw_message,
            status="error",
            ack=err,
            host=device_ip,
            port=port,
            time=now,
        )
        return OrderResult(ok=False, error=err)

    ack_text = ack.replace("\r", "\n") if ack else ""
    logger.info("[DEVICE <--] Received ACK from %s:%d (mode=%s)", device_ip, port, orm_mode)
    await store.insert(
        "sent",
        message=raw_message,
        status="success",
        ack=ack_text,
        host=device_ip,
        port=port,
        time=now,
    )
    return OrderResult(ok=True, ack=ack_text)
