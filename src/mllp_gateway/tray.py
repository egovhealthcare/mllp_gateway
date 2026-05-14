"""System tray icon with status-driven menu."""

from __future__ import annotations

import logging
import sys
from enum import Enum
from typing import TYPE_CHECKING, Callable

from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

from mllp_gateway import __version__

if TYPE_CHECKING:
    from mllp_gateway.updater import UpdateInfo

__all__ = ["Status", "TrayApp"]

log = logging.getLogger(__name__)


class Status(Enum):
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERROR = "error"


_COLORS = {
    Status.STARTING: "#FFA500",
    Status.RUNNING: "#22C55E",
    Status.DEGRADED: "#EAB308",
    Status.ERROR: "#EF4444",
}


def _dot(color: str, size: int = 64) -> Image.Image:
    """Render a colored circle on a transparent background for the tray icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m = size // 8
    ImageDraw.Draw(img).ellipse([m, m, size - m, size - m], fill=color)
    return img


def _platform_setup() -> None:
    if sys.platform == "darwin":
        try:
            import AppKit

            app = AppKit.NSApplication.sharedApplication()
            app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        except ImportError:
            log.warning("AppKit unavailable — dock icon may appear")


class TrayApp:
    """System tray application with a status-colored icon and dynamic menu.

    All ``update_*`` methods may be called from any thread; pystray handles
    the cross-thread icon/menu refresh internally.
    """

    def __init__(
        self,
        on_restart: Callable[[], None],
        on_exit: Callable[[], None],
        on_open_config: Callable[[], None],
        on_open_ui: Callable[[], None],
    ):
        self._on_restart = on_restart
        self._on_exit = on_exit
        self._on_open_config = on_open_config
        self._on_open_ui = on_open_ui

        self._status = Status.STARTING
        self._tunnel_connected = False
        self._care_api_connected = False
        self._device_count = 0
        self._update_info: UpdateInfo | None = None
        self._icon: Icon | None = None

    @property
    def status(self) -> Status:
        return self._status

    def update_status(self, status: Status) -> None:
        self._status = status
        self._refresh()

    def update_tunnel(self, connected: bool) -> None:
        self._tunnel_connected = connected
        self._refresh()

    def update_care_api(self, connected: bool) -> None:
        self._care_api_connected = connected
        if not connected and self._status == Status.RUNNING:
            self._status = Status.DEGRADED
        elif connected and self._status == Status.DEGRADED:
            self._status = Status.RUNNING
        self._refresh()

    def update_connections(self, device_count: int) -> None:
        self._device_count = device_count
        self._refresh()

    def update_available(self, info: UpdateInfo) -> None:
        self._update_info = info
        self._refresh()

    def _refresh(self) -> None:
        if self._icon and self._icon.visible:
            self._icon.icon = _dot(_COLORS[self._status])
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def _build_menu(self) -> Menu:
        items = [
            # Hidden default action — makes left click open the menu
            MenuItem("Open", None, default=True, visible=False),
            MenuItem(f"MLLP Gateway v{__version__}", None, enabled=False),
            MenuItem(f"Status: {self._status.value}", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Open Web UI", lambda: self._on_open_ui()),
        ]

        if self._update_info is not None:
            if self._update_info.is_breaking:
                items.append(
                    MenuItem(
                        f"v{self._update_info.version} available (manual update)",
                        None,
                        enabled=False,
                    )
                )
            else:
                items.append(
                    MenuItem(
                        f"Update to v{self._update_info.version} (restart)",
                        lambda: self._on_restart(),
                    )
                )

        tunnel = "Connected" if self._tunnel_connected else "Disconnected"
        care_api = "Connected" if self._care_api_connected else "Disconnected"
        items += [
            Menu.SEPARATOR,
            MenuItem(f"CARE API: {care_api}", None, enabled=False),
            MenuItem(f"Tunnel: {tunnel}", None, enabled=False),
            MenuItem(f"Devices: {self._device_count}", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Restart", lambda: self._on_restart()),
            MenuItem("Open Config", lambda: self._on_open_config()),
            MenuItem("Exit", lambda: self._on_exit()),
        ]
        return Menu(*items)

    def run_blocking(self, on_ready: Callable[[], None] | None = None) -> None:
        _platform_setup()

        menu = self._build_menu()
        self._icon = Icon(
            name="mllp-gateway",
            icon=_dot(_COLORS[self._status]),
            title="MLLP Gateway",
            menu=menu,
        )

        def setup(icon: Icon) -> None:
            icon.visible = True
            if on_ready:
                on_ready()

        self._icon.run(setup=setup)

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
