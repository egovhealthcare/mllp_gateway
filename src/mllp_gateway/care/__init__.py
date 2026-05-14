"""CARE backend integration — REST API, HTTP client, and auth."""

from mllp_gateway.care.api import create_app
from mllp_gateway.care.client import CareClient

__all__ = ["CareClient", "create_app"]
