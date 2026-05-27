"""HTTP middleware for the CARE-facing API: CORS, auth, and request logging."""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)

_SKIP_LOG_PREFIXES = ("/health/status", "/ping")


async def on_prepare_cors(request: web.Request, response: web.StreamResponse) -> None:
    """Attach CORS headers to every response (including redirects/errors).

    Register via ``app.on_response_prepare.append(on_prepare_cors)``.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Gateway-Id"
    if request.method == "OPTIONS":
        response.headers["Access-Control-Max-Age"] = "86400"


@web.middleware
async def options_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Return 204 for any OPTIONS request (CORS preflight)."""
    if request.method == "OPTIONS":
        return web.Response(status=204)
    return await handler(request)


@web.middleware
async def request_logging_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Log incoming API requests with direction, skipping health checks."""
    if request.path.rstrip("/").startswith(_SKIP_LOG_PREFIXES):
        return await handler(request)
    logger.info("[API <--] %s %s from %s", request.method, request.path, request.remote)
    resp = await handler(request)
    logger.info("[API -->] %s %s -> %d", request.method, request.path, resp.status)
    return resp


@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Verify CARE JWT tokens on endpoints marked with @auth_required."""
    if request.app.get("disable_auth") or not getattr(handler, "_auth_required", False):
        return await handler(request)

    auth = request.app["auth"]
    header = request.headers.get("Authorization", "")
    if not header.startswith("Care_Bearer "):
        return web.json_response(
            {"error": "missing or invalid Authorization header"}, status=401
        )

    token = header.removeprefix("Care_Bearer ")
    try:
        auth.verify_care_token(token)
    except Exception as e:
        logger.warning("Auth failed: %s", e)
        return web.json_response({"error": "invalid token"}, status=401)

    return await handler(request)
