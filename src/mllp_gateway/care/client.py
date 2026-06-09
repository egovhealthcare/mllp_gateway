import asyncio
import logging
from typing import Any

import aiohttp

from mllp_gateway.care.auth import Auth
from mllp_gateway.ssl_context import aiohttp_connector

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.5  # seconds


class CareClient:
    """Async HTTP client for the CARE backend.

    Call :meth:`start` to create the underlying aiohttp session and
    :meth:`close` to tear it down. Transient 502/503/504 and connection
    errors are retried with exponential backoff.
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
            connector=aiohttp_connector(),
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

    async def forward_result(
        self,
        raw_message: str,
        sender_ip: str,
        *,
        sender_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Forward an HL7/ASTM result to CARE with retries on transient failures.

        *sender_device_id* identifies the source device directly; it is
        required for serial-attached analyzers, which have no IP for CARE to
        match against ``endpoint_address``.
        """
        if not self._session or self._session.closed:
            raise RuntimeError("CareClient session not active — call start() first")

        logger.info(
            "[CARE -->] POST /api/lab_analyzer_device/communication/receive_result/ "
            "(sender_ip=%s, sender_device_id=%s)",
            sender_ip,
            sender_device_id,
        )
        payload: dict[str, Any] = {"raw_message": raw_message, "sender_ip": sender_ip}
        if sender_device_id:
            payload["sender_device_id"] = sender_device_id
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with self._session.post(
                    "/api/lab_analyzer_device/communication/receive_result/",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if resp.status in (502, 503, 504):
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
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.error(
                            "[CARE <--] %d error for forward_result (sender_ip=%s): %s",
                            resp.status,
                            sender_ip,
                            body[:500],
                        )
                        resp.raise_for_status()
                    result = await resp.json()
                    logger.info("[CARE <--] 200 OK for forward_result (sender_ip=%s)", sender_ip)
                    return result
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

        raise last_exc or RuntimeError("forward_result failed after retries")

    async def fetch_pending_orders(
        self,
        sender_ip: str,
        sample_ids: list[str],
        message_control_id: str | None = None,
        raw_message: str | None = None,
        *,
        sender_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch pending orders from CARE for query-based analyzers.

        Called when an analyzer sends ORM^O01 or QRY^Q02 to query worklist
        information. Returns order data used to build ORR^O02 responses.
        """
        if not self._session or self._session.closed:
            raise RuntimeError("CareClient session not active — call start() first")

        logger.info(
            "[CARE -->] POST /api/lab_analyzer_device/communication/pending_orders/ "
            "(sender_ip=%s, sample_ids=%s)",
            sender_ip,
            sample_ids,
        )
        payload: dict[str, Any] = {"sender_ip": sender_ip, "sample_ids": sample_ids}
        if message_control_id:
            payload["message_control_id"] = message_control_id
        if raw_message:
            payload["raw_message"] = raw_message
        if sender_device_id:
            payload["sender_device_id"] = sender_device_id

        async with self._session.post(
            "/api/lab_analyzer_device/communication/pending_orders/",
            headers=self._headers(),
            json=payload,
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error(
                    "[CARE <--] %d error for fetch_pending_orders (sender_ip=%s): %s",
                    resp.status,
                    sender_ip,
                    body[:500],
                )
                resp.raise_for_status()
            result = await resp.json()
            logger.info(
                "[CARE <--] %d for fetch_pending_orders (sender_ip=%s)",
                resp.status,
                sender_ip,
            )
            return result

    async def fetch_configured_devices(self) -> list[dict[str, Any]]:
        """Fetch the list of devices configured for this gateway from CARE.

        Returns a list of device dicts with id, registered_name,
        endpoint_address, type, and orm_mode. Returns an empty list on failure.
        """
        if not self._session or self._session.closed:
            return []

        try:
            logger.info("[CARE -->] GET /api/lab_analyzer_device/communication/")
            async with self._session.get(
                "/api/lab_analyzer_device/communication/",
                headers=self._headers(),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(
                        "[CARE <--] %d error for fetch_configured_devices: %s",
                        resp.status,
                        body[:500],
                    )
                    return []
                result = await resp.json()
                logger.info("[CARE <--] 200 OK — %d configured devices", len(result))
                return result
        except Exception as e:
            logger.warning("Failed to fetch configured devices: %s", e)
            return []
