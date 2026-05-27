"""HL7 error response builders for worklist queries."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import hl7

logger = logging.getLogger(__name__)


def build_orr_error_response(original_control_id: str) -> str:
    """Build an ORR^O02 error response when no orders are found."""
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    segments = [
        f"MSH|^~\\&|LIS||||{now}||ORR^O02"
        f"|{original_control_id}|P|2.3.1||||||UNICODE||",
        f"MSA|AE|{original_control_id}",
    ]
    raw = "\r".join(segments)
    message = hl7.parse(raw)
    return str(message)


def build_qck_error_response(original_control_id: str) -> str:
    """Build a QCK^Q02 error response when no orders are found (ADX AutoChem 200)."""
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    segments = [
        f"MSH|^~\\&|LIS||||{now}||QCK^Q02"
        f"|{original_control_id}|P|2.3.1||||||UNICODE",
        f"MSA|AE|{original_control_id}",
        "ERR|0",
    ]
    raw = "\r".join(segments)
    message = hl7.parse(raw)
    return str(message)
