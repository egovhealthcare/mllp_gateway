"""launchd plist management for macOS."""

import subprocess
import textwrap
from pathlib import Path

from mllp_gateway.service.common import APP_DIR, SERVICE_NAME, _get_executable

_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
_LABEL = f"network.ohc.{SERVICE_NAME}"
_PLIST = _LAUNCH_AGENTS / f"{_LABEL}.plist"

_STDOUT_LOG = APP_DIR / "gateway.stdout.log"
_STDERR_LOG = APP_DIR / "gateway.stderr.log"


def install() -> None:
    parts = [*_get_executable(), "run"]

    args_xml = "\n".join(f"        <string>{p}</string>" for p in parts)

    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
{args_xml}
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>ProcessType</key>
            <string>Interactive</string>
            <key>StandardOutPath</key>
            <string>{_STDOUT_LOG}</string>
            <key>StandardErrorPath</key>
            <string>{_STDERR_LOG}</string>
        </dict>
        </plist>
    """)

    _LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    _PLIST.write_text(plist)
    subprocess.run(
        ["launchctl", "load", "-w", str(_PLIST)], check=True, capture_output=True
    )
    print("Service installed and started (launchd).")
    print(f"  Plist: {_PLIST}")


def uninstall() -> None:
    subprocess.run(["launchctl", "unload", str(_PLIST)], capture_output=True)
    if _PLIST.exists():
        _PLIST.unlink()
    print("Service removed.")


def status() -> None:
    result = subprocess.run(
        ["launchctl", "list", _LABEL], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Service is loaded.")
        print(result.stdout)
    else:
        print("Service is not loaded.")


def ensure() -> None:
    if not _PLIST.exists():
        install()
        return
    result = subprocess.run(["launchctl", "list", _LABEL], capture_output=True)
    if result.returncode == 0:
        print("MLLP Gateway service is already running.")
        return
    subprocess.run(["launchctl", "load", "-w", str(_PLIST)], capture_output=True)
    print("Service started.")
