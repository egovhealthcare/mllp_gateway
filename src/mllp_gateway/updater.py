"""Self-update via GitHub releases: check, download, and replace the binary."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from mllp_gateway import __version__
from mllp_gateway.process import is_frozen, restart_process

__all__ = [
    "UpdateInfo",
    "auto_update_and_restart",
    "check_for_update",
    "cmd_check_update",
    "cmd_update",
    "download_and_apply",
    "periodic_update_check",
]

logger = logging.getLogger(__name__)


def _parse_version(tag: str) -> tuple[int, int, int]:
    """Parse a semver tag like ``v1.2.3`` into a comparable 3-tuple."""
    tag = tag.lstrip("v")
    parts = tag.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version tag: {tag!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


_OS_MAP = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
_ARCH_MAP = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}


def _get_asset_name() -> str:
    """Return the expected release asset filename for this OS/architecture."""
    plat = sys.platform
    if plat.startswith("linux"):
        plat = "linux"
    machine = platform.machine().lower()

    os_name = _OS_MAP.get(plat)
    arch = _ARCH_MAP.get(machine)
    if not os_name or not arch:
        raise RuntimeError(f"No pre-built binary for {plat}/{machine}")

    name = f"mllp-gateway-{os_name}-{arch}"
    if plat == "win32":
        name += ".exe"
    return name


@dataclass(frozen=True)
class UpdateInfo:
    """Metadata about an available release."""

    version: str
    download_url: str
    asset_name: str
    is_breaking: bool
    release_notes: str
    github_repo: str


async def check_for_update(github_repo: str) -> UpdateInfo | None:
    try:
        current = _parse_version(__version__)
    except ValueError:
        logger.warning(
            "Cannot parse current version %r, skipping update check", __version__
        )
        return None

    try:
        asset_name = _get_asset_name()
    except RuntimeError as exc:
        logger.warning("%s", exc)
        return None

    url = f"https://api.github.com/repos/{github_repo}/releases/latest"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Accept": "application/vnd.github+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 403:
                    logger.debug("GitHub API rate-limited")
                    return None
                resp.raise_for_status()
                data = await resp.json()
    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        return None

    tag = data.get("tag_name", "")
    try:
        latest = _parse_version(tag)
    except ValueError:
        logger.debug("Cannot parse release tag %r", tag)
        return None

    if latest <= current:
        return None

    download_url = ""
    for asset in data.get("assets", []):
        if asset.get("name") == asset_name:
            download_url = asset.get("browser_download_url", "")
            break

    if not download_url:
        logger.debug("No asset %r in release %s", asset_name, tag)
        return None

    return UpdateInfo(
        version=tag.lstrip("v"),
        download_url=download_url,
        asset_name=asset_name,
        is_breaking=(latest[0] != current[0]),
        release_notes=data.get("body", ""),
        github_repo=github_repo,
    )


async def download_and_apply(info: UpdateInfo) -> bool:
    """Download a release asset and replace the running binary in-place.

    On Windows the old binary is renamed to ``.old`` (deleted on next update)
    because a running executable cannot be overwritten.  Returns True on
    success.
    """
    if not is_frozen():
        logger.info(
            "Update v%s available but auto-update requires a binary install. "
            "Download from https://github.com/%s/releases",
            info.version,
            info.github_repo,
        )
        return False

    exe = Path(sys.executable).resolve()
    logger.info("Downloading v%s from %s", info.version, info.download_url)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=exe.parent, prefix=".mllp-gateway-update-", suffix=exe.suffix
    )
    tmp = Path(tmp_path)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                info.download_url, timeout=aiohttp.ClientTimeout(total=300)
            ) as resp:
                resp.raise_for_status()
                with os.fdopen(tmp_fd, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        f.write(chunk)
                tmp_fd = -1

        if sys.platform == "win32":
            old = exe.with_suffix(exe.suffix + ".old")
            if old.exists():
                old.unlink()
            exe.rename(old)
            tmp.rename(exe)
        else:
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(tmp, exe)

        logger.info("Updated to v%s — restart to use the new version", info.version)
        return True

    except Exception:
        logger.exception("Failed to apply update v%s", info.version)
        try:
            if tmp_fd >= 0:
                os.close(tmp_fd)
        except OSError:
            pass
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


async def periodic_update_check(
    stop_event: asyncio.Event,
    github_repo: str,
    interval_hours: int = 6,
    auto_apply: bool = True,
    on_update_available: Callable[[UpdateInfo], None] | None = None,
) -> None:
    # Clean up leftover .old binary from previous Windows update
    if sys.platform == "win32":
        old = Path(sys.executable).with_suffix(Path(sys.executable).suffix + ".old")
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass

    # Initial delay
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=30)
        return
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        info = await check_for_update(github_repo)

        if info is not None:
            if on_update_available:
                on_update_available(info)

            if info.is_breaking:
                logger.warning(
                    "Breaking update v%s available (current: v%s). "
                    "Run 'mllp-gateway update --force' or download from https://github.com/%s/releases",
                    info.version,
                    __version__,
                    github_repo,
                )
            elif auto_apply:
                if await download_and_apply(info):
                    logger.info("Binary replaced, triggering restart")
                    stop_event.set()
                    return
            else:
                logger.info(
                    "Update v%s available (auto_update disabled).", info.version
                )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_hours * 3600)
            return
        except asyncio.TimeoutError:
            pass


def cmd_check_update(config: "Config") -> None:
    """CLI handler: print whether an update is available."""
    info = asyncio.run(check_for_update(config.github_repo))
    if info is None:
        print(f"v{__version__} is up to date.")
        return

    label = "BREAKING " if info.is_breaking else ""
    print(f"Current: v{__version__}")
    print(f"Latest:  v{info.version} ({label}update)")
    if is_frozen():
        print("\nRun 'mllp-gateway update' to apply.")
    else:
        print(f"\nDownload from https://github.com/{config.github_repo}/releases")


def cmd_update(config: "Config", *, force: bool = False) -> None:
    """CLI handler: download and apply an update."""
    info = asyncio.run(check_for_update(config.github_repo))
    if info is None:
        print(f"v{__version__} is up to date.")
        return

    print(f"Current: v{__version__}")
    print(f"Latest:  v{info.version}")

    if info.is_breaking and not force:
        print(
            f"\nv{info.version} is a breaking update. "
            f"Use --force or download from https://github.com/{config.github_repo}/releases"
        )
        return

    if not is_frozen():
        print(
            f"\nAuto-update requires a binary install. Download from https://github.com/{config.github_repo}/releases"
        )
        return

    if asyncio.run(download_and_apply(info)):
        print(f"Updated to v{info.version}. Please restart.")
    else:
        print("Update failed. Check the log for details.")
        sys.exit(1)


def auto_update_and_restart(config: "Config") -> None:
    """Check for a non-breaking update, apply it, and restart the process."""
    info = asyncio.run(check_for_update(config.github_repo))
    if info and not info.is_breaking and asyncio.run(download_and_apply(info)):
        logger.info("Updated to v%s — restarting with new binary", info.version)
        restart_process()
