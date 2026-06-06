"""Manages active ORU/ORM device connections and per-device send state."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, NamedTuple

import hl7.mllp

__all__ = ["ConnectionManager", "IDLE_THRESHOLD_SECONDS"]

logger = logging.getLogger(__name__)

IDLE_THRESHOLD_SECONDS = 300


class Connection(NamedTuple):
    reader: hl7.mllp.HL7StreamReader
    writer: hl7.mllp.HL7StreamWriter
    connected_at: float


class ORMConnection(NamedTuple):
    reader: hl7.mllp.HL7StreamReader
    writer: hl7.mllp.HL7StreamWriter
    response_queue: asyncio.Queue
    connected_at: float


class ConnectionManager:
    """Tracks active ORU/ORM device connections by IP.

    Devices connect via two MLLP channels:
    - ORU (results): device → gateway, always device-initiated.
    - ORM (orders): gateway → device via shared, server, or client mode.

    For shared mode, a temporary Future is registered so the ORU handler
    knows the next inbound message is an ORM ACK rather than a new result.
    """

    def __init__(self) -> None:
        self._oru: dict[str, Connection] = {}
        self._orm: dict[str, ORMConnection] = {}
        self._oru_send_lock: dict[str, asyncio.Lock] = {}
        self._oru_response_future: dict[str, asyncio.Future[hl7.Message]] = {}
        self._last_activity: dict[str, float] = {}
        self._configured_devices: list[dict[str, Any]] = []

    def _close_writer(self, writer: hl7.mllp.HL7StreamWriter) -> None:
        """Close a writer silently, ignoring errors on already-closed sockets."""
        try:
            writer.close()
        except Exception:
            pass

    def register_oru(
        self,
        ip: str,
        reader: hl7.mllp.HL7StreamReader,
        writer: hl7.mllp.HL7StreamWriter,
    ) -> None:
        """Register a new ORU connection, closing any stale one from the same IP."""
        # Close stale connection from same IP if it exists
        old = self._oru.get(ip)
        if old is not None:
            logger.warning("ORU reconnect from %s — closing previous connection", ip)
            self._close_writer(old.writer)

        self._oru[ip] = Connection(reader, writer, time.monotonic())
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

    def get_oru_writer(self, ip: str) -> hl7.mllp.HL7StreamWriter | None:
        conn = self._oru.get(ip)
        return conn.writer if conn else None

    def get_oru_send_lock(self, ip: str) -> asyncio.Lock | None:
        return self._oru_send_lock.get(ip)

    def set_oru_response_future(self, ip: str, fut: asyncio.Future[hl7.Message]) -> None:
        """Register a future to be resolved with the next inbound message."""
        self._oru_response_future[ip] = fut

    def pop_oru_response_future(self, ip: str) -> asyncio.Future[hl7.Message] | None:
        """Remove and return the pending response future, or None."""
        return self._oru_response_future.pop(ip, None)

    def register_orm(
        self,
        ip: str,
        reader: hl7.mllp.HL7StreamReader,
        writer: hl7.mllp.HL7StreamWriter,
    ) -> None:
        old = self._orm.get(ip)
        if old is not None:
            logger.warning("ORM reconnect from %s — closing previous connection", ip)
            self._close_writer(old.writer)

        self._orm[ip] = ORMConnection(reader, writer, asyncio.Queue(), time.monotonic())
        self._last_activity[ip] = time.monotonic()
        logger.info("ORM connected: %s", ip)

    def unregister_orm(self, ip: str) -> None:
        self._orm.pop(ip, None)

    def get_orm_writer(self, ip: str) -> hl7.mllp.HL7StreamWriter | None:
        conn = self._orm.get(ip)
        return conn.writer if conn else None

    def get_orm_reader(self, ip: str) -> hl7.mllp.HL7StreamReader | None:
        conn = self._orm.get(ip)
        return conn.reader if conn else None

    def get_orm_response_queue(self, ip: str) -> asyncio.Queue | None:
        conn = self._orm.get(ip)
        return conn.response_queue if conn else None

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
        # "client" is always available if the user knows the device IP/port
        modes.append("client")
        return modes

    @property
    def device_count(self) -> int:
        return len(set(self._oru) | set(self._orm))

    def set_configured_devices(self, devices: list[dict[str, Any]]) -> None:
        """Store the list of devices configured for this gateway in CARE."""
        self._configured_devices = devices

    @property
    def configured_devices(self) -> list[dict[str, Any]]:
        return self._configured_devices

    def get_configured_device_status(self) -> list[dict[str, Any]]:
        """Return configured devices with their connection status."""
        result = []
        for dev in self._configured_devices:
            key = self._device_key(dev)
            connected = key is not None and (key in self._oru or key in self._orm)
            result.append({
                **dev,
                "connected": connected,
                "oru_connected": key in self._oru if key else False,
                "orm_connected": key in self._orm if key else False,
            })
        return result

    @staticmethod
    def _device_key(dev: dict[str, Any]) -> str | None:
        """Connection key for a configured device.

        Ethernet devices are tracked by ``endpoint_address`` (IP); serial
        devices have no IP and are tracked by ``connection_key`` (their id).
        """
        return dev.get("connection_key") or dev.get("endpoint_address")

    @property
    def all_configured_connected(self) -> bool:
        """True if every configured device is connected."""
        if not self._configured_devices:
            return True
        for dev in self._configured_devices:
            key = self._device_key(dev)
            if key and key not in self._oru and key not in self._orm:
                return False
        return True

    @property
    def configured_connected_count(self) -> int:
        """Count of configured devices that are currently connected."""
        count = 0
        for dev in self._configured_devices:
            key = self._device_key(dev)
            if key and (key in self._oru or key in self._orm):
                count += 1
        return count

    @property
    def configured_device_count(self) -> int:
        """Total number of configured devices."""
        return len(self._configured_devices)
