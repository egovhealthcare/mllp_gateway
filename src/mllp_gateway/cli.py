"""CLI entry point and subcommand dispatch."""

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from mllp_gateway.config import (
    APP_DIR,
    LOG_FILE,
    load_config,
    run_configure,
)

__all__ = ["entrypoint"]
from mllp_gateway.gateway import run as run_gateway
from mllp_gateway.process import (
    acquire_instance_lock,
    hide_console,
    is_frozen,
    owns_console,
)
from mllp_gateway.service import ensure_service, install_service, show_status, uninstall_service
from mllp_gateway.updater import (
    auto_update_and_restart,
    cmd_check_update,
    cmd_update,
)

logger = logging.getLogger("mllp_gateway")


def _setup_logging(*, to_file: bool = False) -> None:
    """Configure root logger with stderr and optional rotating file output."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.stderr is not None:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(fmt)
        root.addHandler(h)

    if to_file:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
        h.setFormatter(fmt)
        root.addHandler(h)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mllp-gateway",
        description="MLLP Gateway — HL7 lab analyzer bridge for CARE",
    )
    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Start the gateway")
    run_p.add_argument(
        "--no-tray", action="store_true", help="Disable system tray icon"
    )

    sub.add_parser("configure", help="Interactive configuration wizard")

    install_p = sub.add_parser("install", help="Install as system service")
    install_p.add_argument(
        "--system",
        action="store_true",
        help="Install as a system-wide systemd unit (Linux only, requires root)",
    )

    uninstall_p = sub.add_parser("uninstall", help="Remove system service")
    uninstall_p.add_argument(
        "--system",
        action="store_true",
        help="Remove the system-wide systemd unit (Linux only, requires root)",
    )

    status_p = sub.add_parser("status", help="Show service status")
    status_p.add_argument(
        "--system",
        action="store_true",
        help="Query the system-wide systemd unit (Linux only)",
    )

    up = sub.add_parser("update", help="Check for / apply updates")
    up.add_argument("--check", action="store_true", help="Only check, don't apply")
    up.add_argument("--force", action="store_true", help="Apply breaking updates")

    return p


def entrypoint() -> None:
    """Main CLI entry point, dispatching to subcommands or the default
    double-click flow (auto-update → ensure service is installed)."""
    parser = _build_parser()
    args = parser.parse_args()

    match args.command:
        case "configure":
            run_configure()
            return
        case "install":
            install_service(system=args.system)
            return
        case "uninstall":
            uninstall_service(system=args.system)
            return
        case "status":
            show_status(system=args.system)
            return

    config = load_config()

    match args.command:
        case "update":
            if args.check:
                cmd_check_update(config)
            else:
                cmd_update(config, force=args.force)
            return

        case "run":
            _setup_logging(to_file=True)
            logger.info(
                "start: argv=%r frozen=%s pid=%d",
                sys.argv,
                is_frozen(),
                os.getpid(),
            )

            if not acquire_instance_lock():
                print("MLLP Gateway is already running.", file=sys.stderr)
                sys.exit(1)

            if not args.no_tray:
                hide_console()

            try:
                run_gateway(config, no_tray=args.no_tray)
            except KeyboardInterrupt:
                pass
            return

        case _:
            # No subcommand (double-click or bare `mllp-gateway`).
            if not is_frozen():
                parser.print_help()
                return

            _setup_logging(to_file=True)
            auto_update_and_restart(config)

            try:
                ensure_service()
            except Exception as exc:
                sys.exit(f"\nError: {exc}")
            finally:
                if owns_console():
                    input("\nPress Enter to close...")
