"""Localhost-only web UI for viewing HL7 messages and connection status."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jinja2
from aiohttp import web

from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import ORM_MODES, dispatch_order

logger = logging.getLogger(__name__)

LifecycleCallback = Callable[[], Any]

_WS_HEARTBEAT = 15  # seconds

_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATE_DIR = _PKG_DIR / "templates"

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def _dumps(obj: object) -> str:
    return json.dumps(obj, default=str)


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
    msg_data: dict[str, Any],
    app: web.Application,
) -> dict[str, Any]:
    """Handle an incoming WS request and return a response dict."""
    store: MessageStore = app["store"]
    connections: ConnectionManager = app["connections"]
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

    if req_type == "get_configured_devices":
        return {
            "type": "configured_devices",
            "id": req_id,
            "data": connections.get_configured_device_status(),
        }

    if req_type == "send":
        return await _ws_handle_send(msg_data, store, connections, req_id)

    if req_type == "refresh_devices":
        return await _ws_handle_refresh_devices(req_id, app)

    if req_type == "restart":
        return await _ws_handle_lifecycle(req_id, app, "restart")

    if req_type == "exit":
        return await _ws_handle_lifecycle(req_id, app, "exit")

    return {
        "type": "error",
        "id": req_id,
        "data": {"error": f"unknown request type: {req_type}"},
    }


async def _ws_handle_send(
    msg_data: dict[str, Any],
    store: MessageStore,
    connections: ConnectionManager,
    req_id: Any,
) -> dict[str, Any]:
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
                resp = await _ws_handle_request(msg_data, request.app)
                await ws_resp.send_str(_dumps(resp))
            elif ws_msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                break
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        push_task.cancel()
        store.unsubscribe(queue)

    return ws_resp


async def _ws_handle_refresh_devices(
    req_id: Any, app: web.Application
) -> dict[str, Any]:
    """Trigger a device list refresh from CARE."""
    callback: LifecycleCallback | None = app.get("on_refresh_devices")
    if not callback:
        return {"type": "refresh_devices", "id": req_id, "data": {"error": "not available"}}
    try:
        count = await callback()
        return {
            "type": "refresh_devices",
            "id": req_id,
            "data": {"ok": True, "count": count},
        }
    except Exception as e:
        logger.exception("refresh_devices failed")
        return {"type": "refresh_devices", "id": req_id, "data": {"error": str(e)}}


async def _ws_handle_lifecycle(
    req_id: Any, app: web.Application, action: str
) -> dict[str, Any]:
    """Invoke the on_restart or on_exit callback provided by the gateway."""
    callback: LifecycleCallback | None = app.get(f"on_{action}")
    if not callback:
        return {"type": action, "id": req_id, "data": {"error": "not available"}}
    callback()
    return {"type": action, "id": req_id, "data": {"ok": True}}


def create_ui_app(
    store: MessageStore,
    connections: ConnectionManager,
    *,
    on_restart: LifecycleCallback | None = None,
    on_exit: LifecycleCallback | None = None,
    on_refresh_devices: LifecycleCallback | None = None,
) -> web.Application:
    """Build the localhost-only aiohttp application for the web UI."""
    app = web.Application()
    app["store"] = store
    app["connections"] = connections
    app["on_restart"] = on_restart
    app["on_exit"] = on_exit
    app["on_refresh_devices"] = on_refresh_devices

    app.router.add_get("/", _handle_index)
    app.router.add_get("/message/{id}", _handle_message_detail)
    app.router.add_get("/ws", _handle_ws)
    app.router.add_static("/static", _STATIC_DIR, show_index=False)

    return app
