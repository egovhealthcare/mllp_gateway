"""Localhost-only web UI for viewing HL7 messages and connection status."""

import asyncio
import json
import logging
from pathlib import Path

import jinja2
from aiohttp import web

from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import ORM_MODES, dispatch_order

logger = logging.getLogger(__name__)

_WS_HEARTBEAT = 15  # seconds

_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATE_DIR = _PKG_DIR / "templates"

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def _dumps(obj: object) -> str:
    return json.dumps(obj, default=_json_default)


def _json_default(obj):
    # Fallback serializer: converts datetimes, Paths, etc. to strings
    # rather than crashing. Intentionally broad — the web UI is debug-facing.
    return str(obj)


async def _handle_index(request: web.Request) -> web.Response:
    template = _jinja_env.get_template("index.html")
    html = template.render()
    return web.Response(text=html, content_type="text/html")


async def _handle_message_detail(request: web.Request) -> web.Response:
    store: MessageStore = request.app["store"]
    msg_id = int(request.match_info["id"])
    msg = await store.get_message_by_id(msg_id)
    if not msg:
        return web.Response(text="Message not found", status=404)

    template = _jinja_env.get_template("message.html")
    html = template.render(msg=msg)
    return web.Response(text=html, content_type="text/html")


async def _ws_handle_request(
    msg_data: dict, store: MessageStore, connections: ConnectionManager
) -> dict:
    """Handle an incoming WS request and return a response dict."""
    req_type = msg_data.get("type", "")
    req_id = msg_data.get("id")

    if req_type == "get_messages":
        sent = await store.get_messages("sent")
        received = await store.get_messages("received")
        return {
            "type": "messages",
            "id": req_id,
            "data": {"sent": sent, "received": received},
        }

    if req_type == "get_stats":
        stats = await store.get_stats()
        stats["devices"] = connections.device_count
        return {"type": "stats", "id": req_id, "data": stats}

    if req_type == "get_connections":
        return {
            "type": "connections",
            "id": req_id,
            "data": connections.get_connection_status(),
        }

    if req_type == "send":
        return await _ws_handle_send(msg_data, store, connections, req_id)

    return {
        "type": "error",
        "id": req_id,
        "data": {"error": f"unknown request type: {req_type}"},
    }


async def _ws_handle_send(
    msg_data: dict, store: MessageStore, connections: ConnectionManager, req_id
) -> dict:
    raw_message = (msg_data.get("message") or "").strip()
    host = (msg_data.get("host") or "").strip()
    mode = (msg_data.get("mode") or "client").strip()

    if not raw_message:
        return {
            "type": "send_result",
            "id": req_id,
            "data": {"error": "message is required"},
        }
    if not host:
        return {
            "type": "send_result",
            "id": req_id,
            "data": {"error": "host is required"},
        }

    if mode not in ORM_MODES:
        return {
            "type": "send_result",
            "id": req_id,
            "data": {"error": f"mode must be one of {', '.join(sorted(ORM_MODES))}"},
        }

    try:
        port = int(msg_data.get("port", 2575))
    except (TypeError, ValueError):
        return {
            "type": "send_result",
            "id": req_id,
            "data": {"error": "port must be a valid integer"},
        }
    if not 1 <= port <= 65535:
        return {
            "type": "send_result",
            "id": req_id,
            "data": {"error": "port must be 1–65535"},
        }

    result = await dispatch_order(connections, store, host, port, raw_message, mode)
    if not result.ok:
        return {"type": "send_result", "id": req_id, "data": {"error": result.error}}
    return {
        "type": "send_result",
        "id": req_id,
        "data": {"ok": True, "ack": result.ack},
    }


async def _handle_ws(request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint: streams real-time events and handles UI commands."""
    ws_resp = web.WebSocketResponse(heartbeat=_WS_HEARTBEAT)
    await ws_resp.prepare(request)

    store: MessageStore = request.app["store"]
    connections: ConnectionManager = request.app["connections"]
    queue = store.subscribe()

    async def _push_events():
        """Forward store events to the client."""
        try:
            while not ws_resp.closed:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=30)
                    await ws_resp.send_str(_dumps({"event": event, "data": data}))
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass

    push_task = asyncio.create_task(_push_events())

    try:
        async for ws_msg in ws_resp:
            if ws_msg.type == web.WSMsgType.TEXT:
                try:
                    msg_data = json.loads(ws_msg.data)
                except (json.JSONDecodeError, TypeError):
                    await ws_resp.send_str(
                        _dumps({"type": "error", "data": {"error": "invalid JSON"}})
                    )
                    continue
                resp = await _ws_handle_request(msg_data, store, connections)
                await ws_resp.send_str(_dumps(resp))
            elif ws_msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                break
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        push_task.cancel()
        store.unsubscribe(queue)

    return ws_resp


class _StaticFilter(logging.Filter):
    """Suppress noisy access-log entries for static asset requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/static/" not in msg


def create_ui_app(
    store: MessageStore, connections: ConnectionManager
) -> web.Application:
    """Build the localhost-only aiohttp application for the web UI."""
    app = web.Application()
    app["store"] = store
    app["connections"] = connections

    app.router.add_get("/", _handle_index)
    app.router.add_get("/message/{id}", _handle_message_detail)
    app.router.add_get("/ws", _handle_ws)
    app.router.add_static("/static", _STATIC_DIR, show_index=False)

    logging.getLogger("aiohttp.access").addFilter(_StaticFilter())

    return app
