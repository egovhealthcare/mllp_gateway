"""Gateway orchestration: starts all servers, background tasks, and the tray."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable

from aiohttp import web

import hl7

__all__ = ["run"]

from mllp_gateway.care import CareClient, create_app
from mllp_gateway.config import CONFIG_FILE, Config
from mllp_gateway.connection_manager import ConnectionManager
from mllp_gateway.message_store import MessageStore
from mllp_gateway.mllp import start_orm_server, start_oru_server
from mllp_gateway.mllp.worklist import (
    build_orr_error_response,
    build_qck_error_response,
)
from mllp_gateway.process import restart_process
from mllp_gateway.updater import periodic_update_check
from mllp_gateway.web import create_ui_app

logger = logging.getLogger(__name__)

# Background task intervals (seconds)
_PURGE_INTERVAL = 6 * 3600  # 6 hours
_RETRY_INTERVAL = 60
_RETRY_BATCH_SIZE = 50
_CARE_PING_INTERVAL = 60
_CONNECTIONS_POLL_INTERVAL = 5


async def _poll_until_stopped(
    stop_event: asyncio.Event, interval: float, callback: Callable[[], None]
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

    Catches messages that failed all immediate retries (e.g., CARE was
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
                    break  # CARE likely still down
        except Exception as e:
            logger.error("Error in retry worker: %s", e)


def _extract_sample_ids(msg: hl7.Message, msg_type: str) -> list[str]:
    """Extract sample IDs from an HL7 worklist query message.

    QRY^Q02 (ADX AutoChem 200): barcode in QRD-8.
    ORM^O01: sample IDs from ORC-2 (placer) or ORC-3 (filler).
    """
    if msg_type == "QRY^Q02":
        try:
            barcode = str(msg.segment("QRD")(8)).strip()
            return [barcode] if barcode else []
        except (KeyError, IndexError):
            return []

    sample_ids: list[str] = []
    for segment in msg:
        if str(segment(0)) == "ORC":
            sample_id = str(segment(2)).strip() or str(segment(3)).strip()
            if sample_id:
                sample_ids.append(sample_id)
    return sample_ids


async def _send_hl7_response(
    writer: hl7.mllp.HL7StreamWriter, response: str | list[str]
) -> None:
    """Write one or more HL7 messages to the MLLP writer."""
    if isinstance(response, list):
        for msg_str in response:
            writer.writemessage(hl7.parse(msg_str))
            await writer.drain()
    else:
        writer.writemessage(hl7.parse(response))
        await writer.drain()


async def _handle_worklist_query(
    care: CareClient,
    msg: hl7.Message,
    peer_ip: str,
    writer: hl7.mllp.HL7StreamWriter,
) -> None:
    """Handle a worklist query (ORM^O01 or QRY^Q02) from a lab analyzer.

    Fetches pending orders from CARE and responds with the appropriate
    HL7 message (ORR^O02, QCK^Q02, or a device-specific pre-built response).
    """
    try:
        msg_type = str(msg.segment("MSH")(9))
        control_id = str(msg.segment("MSH")(10))
        sample_ids = _extract_sample_ids(msg, msg_type)
        raw_message = str(msg).replace("\r", "\n")

        try:
            result = await care.fetch_pending_orders(
                peer_ip, sample_ids, control_id, raw_message
            )
        except Exception as e:
            logger.error("Failed to fetch pending orders from CARE: %s", e)
            result = {}

        if not sample_ids or not result.get("orders"):
            error_response = (
                build_qck_error_response(control_id)
                if msg_type == "QRY^Q02"
                else build_orr_error_response(control_id)
            )
            await _send_hl7_response(writer, error_response)
            return

        raw_hl7_response = result.get("raw_hl7_response")
        if raw_hl7_response:
            await _send_hl7_response(writer, raw_hl7_response)
        else:
            # Backend didn't build a response — send error acknowledgement
            error_response = (
                build_qck_error_response(control_id)
                if msg_type == "QRY^Q02"
                else build_orr_error_response(control_id)
            )
            await _send_hl7_response(writer, error_response)
    except Exception as e:
        logger.error("Worklist query handler error for %s: %s", peer_ip, e)


async def run_gateway(
    config: Config,
    stop_event: asyncio.Event,
    *,
    on_update_available: Callable | None = None,
    on_tunnel_started: Callable[[], None] | None = None,
    on_connections_changed: Callable[[int], None] | None = None,
    on_care_api_status: Callable[[bool], None] | None = None,
    on_configured_devices_status: Callable[[bool, int, int], None] | None = None,
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

    async def worklist_handler(
        msg: hl7.Message, peer_ip: str, writer: hl7.mllp.HL7StreamWriter
    ) -> None:
        await _handle_worklist_query(care, msg, peer_ip, writer)

    oru_server = await start_oru_server(
        "0.0.0.0", config.oru_port, connections, care.forward_result, store,
        worklist_handler=worklist_handler,
    )

    orm_server = await start_orm_server(
        "0.0.0.0", config.orm_port, connections, store,
        worklist_handler=worklist_handler,
    )

    app = create_app(care, connections, store, disable_auth=config.disable_auth)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()

    # Localhost-only web UI
    ui_app = create_ui_app(store, connections)
    ui_runner = web.AppRunner(ui_app, access_log=None)
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

    # Fetch configured devices from CARE (after API + tunnel are up so JWT
    # validation callback can reach our OpenID endpoint)
    if tunnel_proc:
        await asyncio.sleep(3)  # Give tunnel time to register with Cloudflare
    configured_devices = await care.fetch_configured_devices()
    connections.set_configured_devices(configured_devices)
    if configured_devices:
        logger.info("Loaded %d configured devices from CARE", len(configured_devices))
    else:
        logger.warning("No configured devices fetched from CARE (will retry periodically)")

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
    if on_configured_devices_status:
        tasks.append(
            asyncio.create_task(
                _poll_until_stopped(
                    stop_event,
                    _CONNECTIONS_POLL_INTERVAL,
                    lambda: on_configured_devices_status(
                        connections.all_configured_connected,
                        connections.configured_connected_count,
                        connections.configured_device_count,
                    ),
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

    # Periodically retry fetching configured devices until successful
    async def _refresh_configured_devices():
        while not stop_event.is_set():
            if connections.configured_devices:
                return  # Already have devices, no need to retry
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_CARE_PING_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass
            devices = await care.fetch_configured_devices()
            if devices:
                connections.set_configured_devices(devices)
                logger.info("Loaded %d configured devices from CARE (retry)", len(devices))

    tasks.append(asyncio.create_task(_refresh_configured_devices()))

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
                on_configured_devices_status=tray.update_configured_devices_status,
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
