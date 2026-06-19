"""ASTM message routing: worklist queries and result forwarding to CARE."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from mllp_gateway.astm import ASTMSession
from mllp_gateway.astm import codec as astm_codec
from mllp_gateway.care import CareClient
from mllp_gateway.message_store import MessageStore
from mllp_gateway.transport.device import DeviceConfig

logger = logging.getLogger(__name__)


def _is_astm_query(records: list[str]) -> bool:
    """True if the message is a host-query request (contains a ``Q`` record)."""
    return any(astm_codec.record_type(line) == "Q" for line in records)


def _extract_astm_sample_ids(records: list[str]) -> list[str]:
    """Pull sample/specimen IDs from ``Q`` query records."""
    sample_ids: list[str] = []
    for line in records:
        if astm_codec.record_type(line) != "Q":
            continue
        fields = astm_codec.split_fields(line)
        if len(fields) > 2 and fields[2]:
            sample_ids.append(fields[2].lstrip("^").split("^")[0])
    return [s for s in sample_ids if s]


async def handle_astm_message(
    device: DeviceConfig,
    records: list[str],
    session: ASTMSession,
    care: CareClient,
    store: MessageStore,
) -> None:
    """Route an ASTM message to CARE (results) or back to the analyzer (worklist)."""
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
            await store.insert(
                "sent",
                message=response_text.replace("\r", "\n"),
                status="success" if ok else "error",
                peer=device.connection_key,
                host=device.connection_key,
                time=now,
            )
            logger.info(
                "[DEVICE -->] Sent ASTM worklist (%d records) to %s (ok=%s)",
                len(order_records),
                device.connection_key,
                ok,
            )
        return

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
