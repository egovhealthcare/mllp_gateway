"""SQLite message persistence and pub/sub for real-time UI updates.

All database I/O runs in a thread-pool executor to avoid blocking the
async event loop. Subscribers receive events via asyncio queues; slow
subscribers are dropped to prevent backpressure from stalling writes.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mllp_gateway.config import APP_DIR

__all__ = ["DB_PATH", "MessageStore"]

logger = logging.getLogger(__name__)

DB_PATH = APP_DIR / "messages.db"

_SUBSCRIBER_QUEUE_SIZE = 256


class MessageStore:
    """Async-safe SQLite store with pub/sub event dispatch.

    Call :meth:`init` once after construction to create the schema.
    Use :meth:`subscribe` / :meth:`unsubscribe` to receive real-time
    ``(event, data)`` tuples pushed on every insert or status update.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    async def init(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.run_in_executor(None, self._init_db)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    ack TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    host TEXT NOT NULL DEFAULT '',
                    port INTEGER NOT NULL DEFAULT 0,
                    peer TEXT NOT NULL DEFAULT '',
                    time TEXT NOT NULL,
                    forwarded INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time)"
            )

    def _insert_sync(self, kind: str, **fields) -> dict:
        with self._get_db() as conn:
            cur = conn.execute(
                "INSERT INTO messages (kind, message, ack, status, host, port, peer, time, forwarded) "
                "VALUES (:kind, :message, :ack, :status, :host, :port, :peer, :time, :forwarded)",
                {
                    "kind": kind,
                    "message": fields.get("message", ""),
                    "ack": fields.get("ack", ""),
                    "status": fields.get("status", ""),
                    "host": fields.get("host", ""),
                    "port": fields.get("port", 0),
                    "peer": fields.get("peer", ""),
                    "time": fields.get("time", datetime.now(timezone.utc).isoformat()),
                    "forwarded": fields.get("forwarded", 0),
                },
            )
            row_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (row_id,)
            ).fetchone()
            return dict(row)

    async def insert(self, kind: str, **fields) -> dict:
        row = await self._loop.run_in_executor(
            None, lambda: self._insert_sync(kind, **fields)
        )
        event = "received_message" if kind == "received" else "sent_message"
        self.notify(event, row)
        return row

    def _get_messages_sync(self, kind: str, limit: int) -> list[dict]:
        with self._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    async def get_messages(self, kind: str, limit: int = 200) -> list[dict]:
        return await self._loop.run_in_executor(
            None, lambda: self._get_messages_sync(kind, limit)
        )

    def _get_by_id_sync(self, msg_id: int) -> dict | None:
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (msg_id,)
            ).fetchone()
            return dict(row) if row else None

    async def get_message_by_id(self, msg_id: int) -> dict | None:
        return await self._loop.run_in_executor(
            None, lambda: self._get_by_id_sync(msg_id)
        )

    def _update_forward_sync(self, msg_id: int, forwarded: bool) -> None:
        with self._get_db() as conn:
            conn.execute(
                "UPDATE messages SET forwarded = ? WHERE id = ?",
                (1 if forwarded else 0, msg_id),
            )

    async def update_forward_status(self, msg_id: int, forwarded: bool) -> None:
        await self._loop.run_in_executor(
            None, lambda: self._update_forward_sync(msg_id, forwarded)
        )
        self.notify("forward_status", {"id": msg_id, "forwarded": forwarded})

    def _get_unforwarded_sync(self, limit: int) -> list[dict]:
        with self._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE kind = 'received' AND forwarded = 0 "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    async def get_unforwarded(self, limit: int = 50) -> list[dict]:
        """Return received messages that have not been forwarded to CARE."""
        return await self._loop.run_in_executor(
            None, lambda: self._get_unforwarded_sync(limit)
        )

    def _purge_sync(self, retention_days: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        with self._get_db() as conn:
            cur = conn.execute("DELETE FROM messages WHERE time < ?", (cutoff,))
            return cur.rowcount

    async def purge(self, retention_days: int) -> int:
        count = await self._loop.run_in_executor(
            None, lambda: self._purge_sync(retention_days)
        )
        if count:
            logger.info("Purged %d messages older than %d days", count, retention_days)
        return count

    def _get_stats_sync(self) -> dict:
        with self._get_db() as conn:
            received = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE kind = 'received'"
            ).fetchone()[0]
            sent = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE kind = 'sent'"
            ).fetchone()[0]
            forwarded = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE kind = 'received' AND forwarded = 1"
            ).fetchone()[0]
            return {"received": received, "sent": sent, "forwarded": forwarded}

    async def get_stats(self) -> dict:
        return await self._loop.run_in_executor(None, self._get_stats_sync)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def notify(self, event: str, data: object) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            logger.warning("Dropping subscriber: event queue full")
            self._subscribers.remove(q)
