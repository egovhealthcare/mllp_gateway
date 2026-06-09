"""SSL/TLS configuration for HTTPS clients.

PyInstaller bundles on Windows do not ship Python's default CA bundle, so
TLS verification fails against public HTTPS endpoints (e.g. Let's Encrypt).
We use certifi's Mozilla CA bundle everywhere we make outbound HTTPS calls.
"""

from __future__ import annotations

import os
import ssl
from functools import lru_cache

import certifi

__all__ = [
    "aiohttp_connector",
    "configure_ssl_environment",
    "get_ssl_context",
]


def configure_ssl_environment() -> None:
    """Point process-wide SSL env vars at certifi's CA bundle."""
    cafile = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)


@lru_cache(maxsize=1)
def get_ssl_context() -> ssl.SSLContext:
    configure_ssl_environment()
    return ssl.create_default_context(cafile=certifi.where())


def aiohttp_connector():
    import aiohttp

    return aiohttp.TCPConnector(ssl=get_ssl_context())
