"""Platform-specific service installation (systemd, launchd, schtasks)."""

import sys


def _get_platform_module():
    if sys.platform.startswith("linux"):
        from mllp_gateway.service import _linux

        return _linux
    elif sys.platform == "darwin":
        from mllp_gateway.service import _macos

        return _macos
    elif sys.platform == "win32":
        from mllp_gateway.service import _windows

        return _windows
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def install_service(*, system: bool = False) -> None:
    mod = _get_platform_module()
    if system and hasattr(mod, "install_system"):
        mod.install_system()
    else:
        mod.install()


def uninstall_service(*, system: bool = False) -> None:
    mod = _get_platform_module()
    if system and hasattr(mod, "uninstall_system"):
        mod.uninstall_system()
    else:
        mod.uninstall()


def show_status(*, system: bool = False) -> None:
    mod = _get_platform_module()
    if system and hasattr(mod, "status_system"):
        mod.status_system()
    else:
        mod.status()


def ensure_service() -> None:
    _get_platform_module().ensure()
