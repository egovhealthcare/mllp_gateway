"""systemd user/system unit management for Linux."""

import getpass
import logging
import shlex
import subprocess
import textwrap
from pathlib import Path

from mllp_gateway.service.common import SERVICE_NAME, _get_executable


logger = logging.getLogger(__name__)

_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_USER_UNIT = _USER_DIR / f"{SERVICE_NAME}.service"

_SYSTEM_DIR = Path("/etc/systemd/system")
_SYSTEM_UNIT = _SYSTEM_DIR / f"{SERVICE_NAME}.service"


def install() -> None:
    """Install as a systemd user unit (no root required)."""
    exe = _get_executable()
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=MLLP Gateway
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={shlex.join([*exe, "run"])}
        Restart=on-failure
        RestartSec=5
        Environment=HOME={Path.home()}

        [Install]
        WantedBy=default.target
    """)

    _USER_DIR.mkdir(parents=True, exist_ok=True)
    _USER_UNIT.write_text(unit)
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True, capture_output=True
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", SERVICE_NAME],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.error("systemctl failed: %s", exc.stderr)
        raise

    result = subprocess.run(["loginctl", "enable-linger"], capture_output=True)
    if result.returncode != 0:
        logger.warning("loginctl enable-linger failed: %s", result.stderr)

    print("Service installed and started (systemd user unit).")
    print(f"  Unit file: {_USER_UNIT}")


def install_system() -> None:
    """Install as a system-wide systemd unit (requires root)."""
    exe = _get_executable()
    user = getpass.getuser()
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=MLLP Gateway
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={shlex.join([*exe, "run", "--no-tray"])}
        Restart=on-failure
        RestartSec=5
        User={user}
        Environment=HOME={Path.home()}

        [Install]
        WantedBy=multi-user.target
    """)

    _SYSTEM_UNIT.write_text(unit)
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"], check=True, capture_output=True
        )
        subprocess.run(
            ["systemctl", "enable", "--now", SERVICE_NAME],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.error("systemctl failed: %s", exc.stderr)
        raise

    print("Service installed and started (systemd system unit).")
    print(f"  Unit file: {_SYSTEM_UNIT}")


def uninstall() -> None:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME], capture_output=True
    )
    if _USER_UNIT.exists():
        _USER_UNIT.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("Service removed.")


def uninstall_system() -> None:
    """Remove the system-wide systemd unit (requires root)."""
    subprocess.run(
        ["systemctl", "disable", "--now", SERVICE_NAME], capture_output=True
    )
    if _SYSTEM_UNIT.exists():
        _SYSTEM_UNIT.unlink()
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    print("System service removed.")


def status() -> None:
    result = subprocess.run(
        ["systemctl", "--user", "status", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    print(result.stdout or result.stderr or "Service not found.")


def status_system() -> None:
    """Show status of the system-wide systemd unit."""
    result = subprocess.run(
        ["systemctl", "status", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    print(result.stdout or result.stderr or "System service not found.")


def ensure() -> None:
    # Check system unit first, then user unit
    if _SYSTEM_UNIT.exists():
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "active":
            print("MLLP Gateway system service is already running.")
            return
        subprocess.run(["systemctl", "start", SERVICE_NAME], capture_output=True)
        print("System service started.")
        return

    if not _USER_UNIT.exists():
        install()
        return
    result = subprocess.run(
        ["systemctl", "--user", "is-active", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() == "active":
        print("MLLP Gateway service is already running.")
        return
    subprocess.run(["systemctl", "--user", "start", SERVICE_NAME], capture_output=True)
    print("Service started.")
