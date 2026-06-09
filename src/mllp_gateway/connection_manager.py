"""Manages active ORU/ORM device connections and per-device send state."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, NamedTuple

import hl7

if TYPE_CHECKING:
    from mllp_gateway.mllp.framing import MllpConnection
    from mllp_gateway.transport.device import DeviceConfig

__all__ = ["ConnectionManager", "IDLE_THRESHOLD_SECONDS"]

logger = logging.getLogger(__name__)

IDLE_THRESHOLD_SECONDS = 300


class Connection(NamedTuple):
    conn: MllpConnection
    connected_at: float


class ORMConnection(NamedTuple):
    conn: MllpConnection
    response_queue: asyncio.Queue
    connected_at: float


class ConnectionManager:
    """Tracks active ORU/ORM device connections by IP or connection key.

    Devices connect via two MLLP channels:
    - ORU (results): device -> gateway, always device-initiated.
    - ORM (orders): gateway -> device via shared, server, or client mode.

    For shared mode, a temporary Future is registered so the ORU handler
    knows the next inbound message is an ORM ACK rather than a new result.
    """

    def __init__(self) -> None:
        self._oru: dict[str, Connection] = {}
        self._orm: dict[str, ORMConnection] = {}
        self._oru_send_lock: dict[str, asyncio.Lock] = {}
        self._oru_response_future: dict[str, asyncio.Future[hl7.Message]] = {}
        self._last_activity: dict[str, float] = {}
        self._configured_devices: list[DeviceConfig] = []

    @staticmethod
    def _close_conn(conn: MllpConnection) -> None:
        try:
            conn.close()
        except Exception:
            pass

    def register_oru(self, ip: str, conn: MllpConnection) -> None:
        """Register a new ORU connection, closing any stale one from the same key."""
        old = self._oru.get(ip)
        if old is not None:
            logger.warning("ORU reconnect from %s — closing previous connection", ip)
            self._close_conn(old.conn)

        self._oru[ip] = Connection(conn, time.monotonic())
        self._oru_send_lock.setdefault(ip, asyncio.Lock())
        self._last_activity[ip] = time.monotonic()
        logger.info("ORU connected: %s", ip)

    def unregister_oru(self, ip: str) -> None:
        self._oru.pop(ip, None)
        self._oru_send_lock.pop(ip, None)
        fut = self._oru_response_future.pop(ip, None)
        if fut and not fut.done():
            fut.cancel()

    def record_activity(self, ip: str) -> None:
        """Update the last-activity timestamp for a device."""
        self._last_activity[ip] = time.monotonic()

    def get_oru_conn(self, ip: str) -> MllpConnection | None:
        entry = self._oru.get(ip)
        return entry.conn if entry else None

    def get_oru_send_lock(self, ip: str) -> asyncio.Lock | None:
        return self._oru_send_lock.get(ip)

    def set_oru_response_future(self, ip: str, fut: asyncio.Future[hl7.Message]) -> None:
        """Register a future to be resolved with the next inbound message."""
        self._oru_response_future[ip] = fut

    def pop_oru_response_future(self, ip: str) -> asyncio.Future[hl7.Message] | None:
        """Remove and return the pending response future, or None."""
        return self._oru_response_future.pop(ip, None)

    def register_orm(self, ip: str, conn: MllpConnection) -> None:
        old = self._orm.get(ip)
        if old is not None:
            logger.warning("ORM reconnect from %s — closing previous connection", ip)
            self._close_conn(old.conn)

        self._orm[ip] = ORMConnection(conn, asyncio.Queue(), time.monotonic())
        self._last_activity[ip] = time.monotonic()
        logger.info("ORM connected: %s", ip)

    def unregister_orm(self, ip: str) -> None:
        self._orm.pop(ip, None)

    def get_orm_conn(self, ip: str) -> MllpConnection | None:
        entry = self._orm.get(ip)
        return entry.conn if entry else None

    def get_orm_response_queue(self, ip: str) -> asyncio.Queue | None:
        entry = self._orm.get(ip)
        return entry.response_queue if entry else None

    def is_connection_alive(self, ip: str) -> bool:
        """Check if a device has been active within the last 5 minutes."""
        last = self._last_activity.get(ip)
        if last is None:
            return False
        return (time.monotonic() - last) < IDLE_THRESHOLD_SECONDS

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        """Return connection status for all known device IPs."""
        ips = sorted(set(self._oru) | set(self._orm))
        return {
            ip: {
                "oru_connected": ip in self._oru,
                "orm_connected": ip in self._orm,
                "send_modes": self._available_send_modes(ip),
                "last_activity_ago_s": round(
                    time.monotonic() - self._last_activity.get(ip, time.monotonic())
                ),
            }
            for ip in ips
        }

    def _available_send_modes(self, ip: str) -> list[str]:
        modes = []
        if ip in self._oru:
            modes.append("shared")
        if ip in self._orm:
            modes.append("server")
        modes.append("client")
        return modes

    @property
    def device_count(self) -> int:
        return len(set(self._oru) | set(self._orm))

    def set_configured_devices(self, devices: list[DeviceConfig]) -> None:
        """Store the list of devices configured for this gateway in CARE."""
        self._configured_devices = devices

    @property
    def configured_devices(self) -> list[DeviceConfig]:
        return self._configured_devices

    def _is_connected(self, key: str) -> bool:
        return key in self._oru or key in self._orm

    def get_configured_device_status(self) -> list[dict[str, Any]]:
        """Return configured devices with their connection status."""
        result = []
        for dev in self._configured_devices:
            key = dev.connection_key
            result.append({
                **dev.raw,
                "connection_key": key,
                "connected": self._is_connected(key),
                "oru_connected": key in self._oru,
                "orm_connected": key in self._orm,
            })
        return result

    @property
    def all_configured_connected(self) -> bool:
        """True if every configured device is connected."""
        return all(
            self._is_connected(dev.connection_key)
            for dev in self._configured_devices
        ) if self._configured_devices else True

    @property
    def configured_connected_count(self) -> int:
        return sum(
            1 for dev in self._configured_devices
            if self._is_connected(dev.connection_key)
        )

    @property
    def configured_device_count(self) -> int:
        return len(self._configured_devices)
