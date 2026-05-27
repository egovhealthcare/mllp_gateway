"""ORM (order) dispatch to lab analyzers over MLLP."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import hl7
import hl7.mllp

from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp.common import validate_hl7

logger = logging.getLogger(__name__)

ORM_MODES = frozenset({"shared", "server", "client"})

# Default timeout for waiting on a device response (seconds).
# Some analyzers are slow; this should be generous.
DEFAULT_ORM_TIMEOUT = 30


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

    - **shared**: piggyback on the existing ORU connection. A future is
      registered so the ORU handler resolves it with the device’s response.
    - **server**: use the dedicated ORM connection the device opened.
    - **client**: open a new TCP connection to ``device_ip:port``.

    If *shared* has no active ORU connection, falls through to *client*.
    """
    if orm_mode not in ORM_MODES:
        raise ValueError(
            f"orm_mode must be one of {', '.join(sorted(ORM_MODES))}, got {orm_mode!r}"
        )

    validation_err = validate_hl7(raw_hl7_message)
    if validation_err:
        raise ValueError(validation_err)

    if orm_mode == "shared":
        # Shared mode: piggyback on the existing ORU connection to send ORM.
        # A future is registered so the ORU server handler knows the next
        # inbound message is the ORM ACK (not a new result). The future is
        # resolved by the ORU handler when the device responds.
        writer = connections.get_oru_writer(device_ip)
        lock = connections.get_oru_send_lock(device_ip)
        if writer and lock:
            async with lock:
                fut = asyncio.get_running_loop().create_future()
                connections.set_oru_response_future(device_ip, fut)
                try:
                    writer.writemessage(hl7.parse(raw_hl7_message))
                    await writer.drain()
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
        writer = connections.get_orm_writer(device_ip)
        queue = connections.get_orm_response_queue(device_ip)
        if not (writer and queue):
            raise ConnectionError(
                f"No ORM connection from {device_ip}. "
                "The analyzer must initiate a dedicated ORM connection to use 'server' mode."
            )
        writer.writemessage(hl7.parse(raw_hl7_message))
        await writer.drain()
        try:
            response = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Device {device_ip} did not ACK within {timeout}s on ORM connection. "
                "The analyzer may be busy or unresponsive."
            )
        return str(response)

    # "client" mode or "shared" fallback: open a new TCP connection to the device
    try:
        reader, writer = await asyncio.wait_for(
            hl7.mllp.open_hl7_connection(device_ip, port),
            timeout=10,
        )
    except asyncio.TimeoutError:
        raise ConnectionError(
            f"Could not connect to {device_ip}:{port} within 10s. "
            "Verify the analyzer is listening and the port is correct."
        )
    except OSError as e:
        raise ConnectionError(
            f"Connection refused by {device_ip}:{port}: {e}. "
            "Verify the analyzer is powered on and accepting connections."
        )

    try:
        writer.writemessage(hl7.parse(raw_hl7_message))
        await writer.drain()
        try:
            response = await asyncio.wait_for(reader.readmessage(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Device {device_ip}:{port} did not respond within {timeout}s in client mode. "
                "The analyzer may not support receiving orders."
            )
        return str(response)
    finally:
        writer.close()
        try:
            await writer.writer.wait_closed()
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
