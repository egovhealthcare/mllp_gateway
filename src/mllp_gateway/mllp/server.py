"""MLLP servers for receiving ORU results and ORM connections from analyzers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import hl7
import hl7.mllp

from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp.common import validate_hl7

logger = logging.getLogger(__name__)

# Signature: (raw_message, peer_ip) -> None
ForwardCallback = Callable[[str, str], Awaitable[None]]

# Signature: (msg, peer_ip, writer) -> None
WorklistHandler = Callable[
    [hl7.Message, str, hl7.mllp.HL7StreamWriter], Awaitable[None]
]

_FORWARD_QUEUE_SIZE = 1000

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds — exponential: 2, 4, 8


def _peer_ip(writer: hl7.mllp.HL7StreamWriter) -> str:
    peername = writer.get_extra_info("peername")
    return peername[0] if peername else "unknown"


def _get_message_type(msg: hl7.Message) -> str:
    """Extract MSH-9 (message type) from an HL7 message, e.g. 'ORM^O01'."""
    try:
        return str(msg.segment("MSH")(9))
    except (IndexError, KeyError):
        return ""


async def start_oru_server(
    host: str,
    port: int,
    connections: ConnectionManager,
    forward: ForwardCallback,
    store: MessageStore,
    worklist_handler: WorklistHandler | None = None,
) -> asyncio.Server:
    """Start the MLLP server that receives ORU (result) messages.

    Each connection gets a per-device forward queue and background worker
    that retries CARE API forwarding with exponential backoff.

    If *worklist_handler* is provided, QRY^Q02 and ORM^O01 messages on
    this port are routed to it instead of being forwarded as results
    (supports single-port analyzers like ADX AutoChem 200).
    """

    async def handler(
        reader: hl7.mllp.HL7StreamReader, writer: hl7.mllp.HL7StreamWriter
    ):
        ip = _peer_ip(writer)
        logger.info("ORU connection from %s", ip)
        connections.register_oru(ip, reader, writer)

        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue(maxsize=_FORWARD_QUEUE_SIZE)
        worker = asyncio.create_task(_forward_worker(ip, queue, forward, store))

        try:
            while True:
                msg = await reader.readmessage()
                connections.record_activity(ip)
                msg_type = _get_message_type(msg)
                logger.info("[DEVICE <--] Received %s from %s (ORU connection)", msg_type or "message", ip)

                # Shared-mode ORM ACK: a future is waiting for the device's response
                fut = connections.pop_oru_response_future(ip)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                    continue

                # Route worklist queries to handler (single-port analyzers)
                if msg_type in ("ORM^O01", "QRY^Q02") and worklist_handler is not None:
                    raw = str(msg).replace("\r", "\n")
                    await store.insert(
                        "received",
                        message=raw,
                        ack="",
                        peer=ip,
                        time=datetime.now(timezone.utc).isoformat(),
                        forwarded=1,
                    )
                    await worklist_handler(msg, ip, writer)
                    logger.info("[DEVICE -->] Sent worklist response to %s (ORU connection)", ip)
                    continue

                # Non-result messages (ACK^Q03, etc.) — store but don't forward
                if not msg_type.startswith("ORU"):
                    raw = str(msg).replace("\r", "\n")
                    await store.insert(
                        "received",
                        message=raw,
                        ack="",
                        peer=ip,
                        time=datetime.now(timezone.utc).isoformat(),
                        forwarded=1,
                    )
                    logger.info("Stored non-result message %s from %s (not forwarding)", msg_type, ip)
                    continue

                ack = msg.create_ack()
                writer.writemessage(ack)
                await writer.drain()
                logger.info("[DEVICE -->] Sent ACK to %s (ORU connection)", ip)
                raw = str(msg).replace("\r", "\n")
                ack_text = str(ack).replace("\r", "\n")

                if validate_hl7(raw) is not None:
                    logger.warning(
                        "Received malformed HL7 from %s — ACK sent but not forwarding",
                        ip,
                    )
                    continue

                row = await store.insert(
                    "received",
                    message=raw,
                    ack=ack_text,
                    peer=ip,
                    time=datetime.now(timezone.utc).isoformat(),
                )

                # Non-blocking put: if queue is full, the retry worker will
                # pick it up from the store later
                try:
                    queue.put_nowait((row["id"], raw))
                except asyncio.QueueFull:
                    logger.error(
                        "Forward queue full for %s — message %d will be retried from store",
                        ip,
                        row["id"],
                    )
        except asyncio.IncompleteReadError:
            logger.info("ORU connection closed by %s", ip)
        except ConnectionResetError:
            logger.info("ORU connection reset by %s", ip)
        except Exception as e:
            logger.error("ORU handler error (%s): %s", ip, e)
        finally:
            await queue.put(None)
            try:
                await worker
            except Exception:
                pass
            connections.unregister_oru(ip)
            writer.close()
            try:
                await writer.writer.wait_closed()
            except Exception:
                pass

    server = await hl7.mllp.start_hl7_server(handler, host=host, port=port)
    logger.info("MLLP ORU listening on %s:%d", host, port)
    return server


async def start_orm_server(
    host: str,
    port: int,
    connections: ConnectionManager,
    store: MessageStore,
    worklist_handler: WorklistHandler | None = None,
) -> asyncio.Server:
    """Start the MLLP server that accepts ORM (order) connections from analyzers.

    Inbound messages are either ACKs to orders we sent (placed on response
    queue for send_order) or ORM^O01 worklist queries from the analyzer.
    """

    async def handler(
        reader: hl7.mllp.HL7StreamReader, writer: hl7.mllp.HL7StreamWriter
    ):
        ip = _peer_ip(writer)
        logger.info("ORM connection from %s", ip)
        connections.register_orm(ip, reader, writer)
        queue = connections.get_orm_response_queue(ip)

        try:
            while True:
                msg = await reader.readmessage()
                connections.record_activity(ip)

                msg_type = _get_message_type(msg)
                logger.info("[DEVICE <--] Received %s from %s (ORM connection)", msg_type or "message", ip)
                if msg_type in ("ORM^O01", "QRY^Q02") and worklist_handler is not None:
                    raw = str(msg).replace("\r", "\n")
                    await store.insert(
                        "received",
                        message=raw,
                        ack="",
                        peer=ip,
                        time=datetime.now(timezone.utc).isoformat(),
                        forwarded=1,
                    )
                    await worklist_handler(msg, ip, writer)
                    logger.info("[DEVICE -->] Sent worklist response to %s (ORM connection)", ip)
                elif queue is not None:
                    await queue.put(msg)
        except asyncio.IncompleteReadError:
            logger.info("ORM connection closed by %s", ip)
        except ConnectionResetError:
            logger.info("ORM connection reset by %s", ip)
        except Exception as e:
            logger.error("ORM handler error (%s): %s", ip, e)
        finally:
            connections.unregister_orm(ip)
            writer.close()
            try:
                await writer.writer.wait_closed()
            except Exception:
                pass

    server = await hl7.mllp.start_hl7_server(handler, host=host, port=port)
    logger.info("MLLP ORM listening on %s:%d", host, port)
    return server


async def _forward_worker(
    peer_ip: str,
    queue: asyncio.Queue[tuple[int, str] | None],
    forward: ForwardCallback,
    store: MessageStore,
) -> None:
    """Drain the forward queue, retrying each message up to MAX_RETRIES times."""
    while True:
        item = await queue.get()
        if item is None:
            return
        msg_id, raw = item

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await forward(raw, peer_ip)
                logger.info("Forwarded ORU from %s (msg_id=%d)", peer_ip, msg_id)
                await store.update_forward_status(msg_id, True)
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    logger.error(
                        "Forward permanently failed for msg_id=%d from %s after %d attempts: %s",
                        msg_id,
                        peer_ip,
                        MAX_RETRIES,
                        e,
                    )
                    await store.update_forward_status(msg_id, False)
                else:
                    delay = RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        "Forward attempt %d/%d failed for msg_id=%d from %s: %s — retrying in %ds",
                        attempt,
                        MAX_RETRIES,
                        msg_id,
                        peer_ip,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
