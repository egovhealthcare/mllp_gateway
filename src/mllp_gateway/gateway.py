"""Gateway orchestration: starts all servers, background tasks, and the tray."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading

from aiohttp import web

__all__ = ["run"]

from mllp_gateway.care import CareClient, create_app
from mllp_gateway.config import CONFIG_FILE, Config
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import start_orm_server, start_oru_server
from mllp_gateway.process import restart_process
from mllp_gateway.updater import periodic_update_check
from mllp_gateway.web import create_ui_app

logger = logging.getLogger(__name__)

# Background task intervals (seconds)
_PURGE_INTERVAL = 6 * 3600  # 6 hours
_RETRY_INTERVAL = 60
_RETRY_BATCH_SIZE = 50
_CARE_PING_INTERVAL = 30
_CONNECTIONS_POLL_INTERVAL = 5


async def _poll_until_stopped(
    stop_event: asyncio.Event, interval: float, callback
) -> None:
    """Call *callback* every *interval* seconds until *stop_event* is set."""
    while not stop_event.is_set():
        callback()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass


async def _periodic_purge(
    stop_event: asyncio.Event, store, retention_days: int
) -> None:
    """Purge expired messages every 6 hours."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_PURGE_INTERVAL)
            return
        except asyncio.TimeoutError:
            await store.purge(retention_days)


async def _retry_unforwarded(
    stop_event: asyncio.Event,
    store: MessageStore,
    care: CareClient,
) -> None:
    """Periodically retry messages that failed to forward to CARE.

    This catches messages that failed all immediate retries (e.g., CARE was
    down for an extended period). Runs every 60 seconds.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_RETRY_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass

        try:
            unforwarded = await store.get_unforwarded(limit=_RETRY_BATCH_SIZE)
            if not unforwarded:
                continue
            logger.info("Retrying %d unforwarded messages", len(unforwarded))
            for msg in unforwarded:
                if stop_event.is_set():
                    return
                try:
                    await care.forward_result(msg["message"], msg["peer"])
                    await store.update_forward_status(msg["id"], True)
                except Exception as e:
                    logger.debug(
                        "Retry still failing for msg_id=%d: %s", msg["id"], e
                    )
                    break  # CARE likely still down, don't hammer it
        except Exception as e:
            logger.error("Error in retry worker: %s", e)


async def run_gateway(
    config: Config,
    stop_event: asyncio.Event,
    *,
    on_update_available=None,
    on_tunnel_started=None,
    on_connections_changed=None,
    on_care_api_status=None,
) -> None:
    """Core async entry point: wire up all servers and background tasks.

    Blocks until *stop_event* is set, then tears everything down in order.
    """
    connections = ConnectionManager()
    store = MessageStore()
    await store.init()
    await store.purge(config.retention_days)

    care = CareClient(
        base_url=config.care_api_url,
        device_id=config.gateway_device_id,
        private_key_pem=config.private_key_pem,
        timeout=config.care_api_timeout,
    )
    await care.start()

    oru_server = await start_oru_server(
        "0.0.0.0", config.oru_port, connections, care.forward_result, store
    )
    orm_server = await start_orm_server("0.0.0.0", config.orm_port, connections)

    app = create_app(care, connections, store, disable_auth=config.disable_auth)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()

    # Localhost-only web UI
    ui_app = create_ui_app(store, connections)
    ui_runner = web.AppRunner(ui_app)
    await ui_runner.setup()
    ui_site = web.TCPSite(ui_runner, "127.0.0.1", config.ui_port)
    await ui_site.start()
    logger.info("Web UI on http://127.0.0.1:%d", config.ui_port)

    tunnel_proc = None
    if config.tunnel_token:
        from mllp_gateway.tunnel import start_tunnel, stop_tunnel

        tunnel_proc = start_tunnel(config.tunnel_token, config.api_port)
        if on_tunnel_started:
            on_tunnel_started()

    logger.info(
        "API on %s:%d (tunnel=%s)",
        config.api_host,
        config.api_port,
        "yes" if tunnel_proc else "no",
    )

    tasks = [
        asyncio.create_task(
            periodic_update_check(
                config,
                stop_event,
                on_update_available=on_update_available,
            )
        ),
        asyncio.create_task(_periodic_purge(stop_event, store, config.retention_days)),
        asyncio.create_task(_retry_unforwarded(stop_event, store, care)),
    ]
    if on_connections_changed:
        tasks.append(
            asyncio.create_task(
                _poll_until_stopped(
                    stop_event,
                    _CONNECTIONS_POLL_INTERVAL,
                    lambda: on_connections_changed(connections.device_count),
                ),
            )
        )
    if on_care_api_status:

        async def _report_care_api():
            while not stop_event.is_set():
                on_care_api_status(await care.ping())
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=_CARE_PING_INTERVAL)
                    return
                except asyncio.TimeoutError:
                    pass

        tasks.append(asyncio.create_task(_report_care_api()))

    await stop_event.wait()
    logger.info("Shutting down")

    # Stop accepting new MLLP connections
    oru_server.close()
    orm_server.close()
    await oru_server.wait_closed()
    await orm_server.wait_closed()

    if tunnel_proc:
        stop_tunnel(tunnel_proc)
    for t in tasks:
        t.cancel()
    await care.close()
    await runner.cleanup()
    await ui_runner.cleanup()


def run_headless(config: Config) -> None:
    """Run the gateway without a system tray (suitable for services / SSH)."""
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, stop.set)
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    else:
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))
        signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))

    try:
        loop.run_until_complete(run_gateway(config, stop))
    finally:
        loop.close()


def run_with_tray(config: Config) -> None:
    """Run the gateway with a pystray system-tray icon on the main thread.

    pystray requires the main thread for its event loop (macOS/Windows).
    The asyncio gateway runs in a daemon thread and communicates back to
    the tray via call_soon_threadsafe for stop/restart signals.
    """
    from mllp_gateway.tray import Status, TrayApp

    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    restart_requested = False

    def open_config():
        path = str(CONFIG_FILE)
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def open_ui():
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{config.ui_port}")

    def request_restart():
        nonlocal restart_requested
        restart_requested = True
        loop.call_soon_threadsafe(stop.set)

    def uninstall_service():
        from mllp_gateway.service import uninstall_service

        uninstall_service()
        loop.call_soon_threadsafe(stop.set)

    tray = TrayApp(
        on_restart=request_restart,
        on_exit=lambda: loop.call_soon_threadsafe(stop.set),
        on_open_config=open_config,
        on_open_ui=open_ui,
        on_uninstall_service=uninstall_service,
    )

    def asyncio_thread():
        asyncio.set_event_loop(loop)

        async def run():
            await run_gateway(
                config,
                stop,
                on_update_available=tray.update_available,
                on_tunnel_started=lambda: tray.update_tunnel(True),
                on_connections_changed=tray.update_connections,
                on_care_api_status=tray.update_care_api,
            )
            tray.stop()

        loop.run_until_complete(run())
        loop.close()

    def on_ready():
        tray.update_status(Status.RUNNING)
        threading.Thread(target=asyncio_thread, daemon=True).start()

    signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))
    signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))
    tray.run_blocking(on_ready=on_ready)

    if restart_requested:
        restart_process()


def run(config: Config, *, no_tray: bool = False) -> None:
    """Top-level entry: try tray mode, fall back to headless on failure."""
    if no_tray:
        run_headless(config)
        return
    try:
        run_with_tray(config)
    except Exception:
        logger.warning("Tray unavailable, falling back to headless", exc_info=True)
        run_headless(config)
