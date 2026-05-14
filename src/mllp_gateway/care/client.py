import asyncio
import logging

import aiohttp

from mllp_gateway.care.auth import Auth

logger = logging.getLogger(__name__)

# Retry configuration for transient CARE API failures
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.5  # seconds


class CareClient:
    """Async HTTP client for the CARE backend.

    Lifecycle: call :meth:`start` to create the underlying aiohttp session,
    and :meth:`close` to tear it down.  :meth:`forward_result` retries
    transient 502/503/504 and connection errors up to ``_MAX_RETRIES`` times
    with exponential backoff.
    """

    def __init__(
        self,
        base_url: str,
        device_id: str,
        private_key_pem: bytes,
        *,
        timeout: int = 25,
    ):
        self._base_url = base_url
        self._device_id = device_id
        self._auth = Auth(private_key_pem, base_url)
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    @property
    def auth(self) -> Auth:
        return self._auth

    async def start(self) -> None:
        """Create the aiohttp session. Must be called before any requests."""
        self._session = aiohttp.ClientSession(
            base_url=self._base_url,
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )

    async def close(self) -> None:
        """Close the aiohttp session and release resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Gateway_Bearer {self._auth.generate_jwt()}",
            "X-Gateway-Id": self._device_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def ping(self) -> bool:
        if not self._session or self._session.closed:
            return False
        try:
            async with self._session.get(
                "/ping/", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def forward_result(self, raw_message: str, sender_ip: str) -> dict:
        """Forward an HL7 result to the CARE API with retries on transient failures."""
        if not self._session or self._session.closed:
            raise RuntimeError("CareClient session not active — call start() first")

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with self._session.post(
                    "/api/lab_analyzer_device/communication/receive_result/",
                    headers=self._headers(),
                    json={"raw_message": raw_message, "sender_ip": sender_ip},
                ) as resp:
                    if resp.status == 502 or resp.status == 503 or resp.status == 504:
                        # Transient server errors — retry
                        body = await resp.text()
                        last_exc = aiohttp.ClientResponseError(
                            resp.request_info,
                            resp.history,
                            status=resp.status,
                            message=body[:200],
                        )
                        if attempt < _MAX_RETRIES:
                            delay = _RETRY_BACKOFF_BASE**attempt
                            logger.warning(
                                "CARE API returned %d (attempt %d/%d) — retrying in %.1fs",
                                resp.status,
                                attempt,
                                _MAX_RETRIES,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        "CARE API connection error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt,
                        _MAX_RETRIES,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

        raise last_exc or RuntimeError("forward_result failed after retries")
