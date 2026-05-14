"""Manages active ORU/ORM device connections and per-device send state."""

import asyncio
import logging
import time
from typing import NamedTuple

import hl7.mllp

__all__ = ["ConnectionManager", "IDLE_THRESHOLD_SECONDS"]

logger = logging.getLogger(__name__)

# A device is considered idle if no activity for this long.
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
    - ORU (results): device → gateway. Always device-initiated.
    - ORM (orders): gateway → device. Three delivery modes:
        shared — piggyback ORM onto the existing ORU connection;
        server — use a dedicated ORM connection the device opens;
        client — gateway opens a new connection to the device.

    For shared mode, a temporary asyncio.Future is registered so the
    ORU server handler knows the next inbound message is the ORM ACK
    (not a new result). See set_oru_response_future / pop_oru_response_future.

    Reconnection handling: when a device reconnects from the same IP, the
    previous connection is closed gracefully before the new one is registered.
    """

    def __init__(self):
        self._oru: dict[str, Connection] = {}
        self._orm: dict[str, ORMConnection] = {}
        self._oru_send_lock: dict[str, asyncio.Lock] = {}
        self._oru_response_future: dict[str, asyncio.Future] = {}
        self._last_activity: dict[str, float] = {}

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

    def set_oru_response_future(self, ip: str, fut: asyncio.Future) -> None:
        """Register a future the ORU handler will resolve with the next
        inbound message from this device (used for shared-mode ORM ACKs)."""
        self._oru_response_future[ip] = fut

    def pop_oru_response_future(self, ip: str) -> asyncio.Future | None:
        """Remove and return the pending response future, or None if absent."""
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

    def get_connection_status(self) -> dict:
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
