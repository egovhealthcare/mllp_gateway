"""HTTP API endpoints for CARE to send orders to connected analyzers."""

import logging

from aiohttp import web

from mllp_gateway.care.client import CareClient
from mllp_gateway.care.middleware import (
    auth_middleware,
    on_prepare_cors,
    options_middleware,
    request_logging_middleware,
)
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import ORM_MODES, dispatch_order

logger = logging.getLogger(__name__)


def auth_required(handler):
    handler._auth_required = True
    return handler


@auth_required
async def _handle_send_order(request: web.Request) -> web.Response:
    """POST /send-order — dispatch an ORM message to a lab analyzer."""
    connections: ConnectionManager = request.app["connections"]
    store: MessageStore = request.app["store"]
    data = await request.json() if request.body_exists else {}

    device_ip = data.get("device_ip")
    raw_message = data.get("raw_message")
    orm_mode = data.get("orm_mode", "shared")

    if not device_ip or not raw_message:
        return web.json_response(
            {"error": "device_ip and raw_message are required"}, status=400
        )

    try:
        port = int(data.get("port", 2575))
    except (TypeError, ValueError):
        return web.json_response({"error": "port must be a valid integer"}, status=400)
    if not 1 <= port <= 65535:
        return web.json_response({"error": "port must be 1–65535"}, status=400)

    if orm_mode not in ORM_MODES:
        return web.json_response(
            {"error": f"orm_mode must be one of {', '.join(sorted(ORM_MODES))}"},
            status=400,
        )

    result = await dispatch_order(
        connections, store, device_ip, port, raw_message, orm_mode
    )
    if not result.ok:
        return web.json_response({"error": result.error}, status=502)
    return web.json_response({"ack": result.ack})


async def _handle_health_status(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "server": True,
            "database": True,
            "connections": request.app["connections"].get_connection_status(),
        }
    )


async def _handle_openid(request: web.Request) -> web.Response:
    return web.json_response(request.app["auth"].get_public_jwks())


def create_app(
    care_client: CareClient,
    connections: ConnectionManager,
    store: MessageStore,
    *,
    disable_auth: bool = False,
) -> web.Application:
    """Build the aiohttp application for the CARE-facing REST API."""
    if disable_auth:
        logger.warning("API authentication is disabled")
    app = web.Application(
        middlewares=[
            options_middleware,
            request_logging_middleware,
            web.normalize_path_middleware(append_slash=True, remove_slash=False),
            auth_middleware,
        ]
    )
    app.on_response_prepare.append(on_prepare_cors)
    app["auth"] = care_client.auth
    app["connections"] = connections
    app["store"] = store
    app["disable_auth"] = disable_auth
    app.router.add_post("/send-order/", _handle_send_order)
    app.router.add_get("/health/status/", _handle_health_status)
    app.router.add_get("/openid-configuration/", _handle_openid)
    return app
