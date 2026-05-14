"""Shared constants and helpers for service modules."""

import sys

from mllp_gateway.config import APP_DIR

__all__ = ["SERVICE_NAME"]

SERVICE_NAME = "mllp-gateway"


def _get_executable() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "mllp_gateway"]
