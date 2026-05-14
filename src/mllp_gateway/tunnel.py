"""Cloudflare Tunnel management: download, start, and stop cloudflared."""

import io
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.request
from pathlib import Path

__all__ = ["start_tunnel", "stop_tunnel"]

logger = logging.getLogger("mllp_gateway.tunnel")

BIN_DIR = Path.home() / ".mllp_gateway" / "bin"

_DOWNLOAD_URLS: dict[tuple[str, str], str] = {
    (
        "linux",
        "x86_64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    (
        "linux",
        "aarch64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    (
        "linux",
        "armv7l",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
    (
        "darwin",
        "x86_64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    (
        "darwin",
        "arm64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    (
        "win32",
        "AMD64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    (
        "win32",
        "x86_64",
    ): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}


def _platform_key() -> tuple[str, str]:
    """Return a ``(os, arch)`` tuple matching the keys in ``_DOWNLOAD_URLS``."""
    system = sys.platform
    if system.startswith("linux"):
        system = "linux"
    return (system, platform.machine())


def _get_cloudflared_path() -> Path:
    system_path = shutil.which("cloudflared")
    if system_path:
        return Path(system_path)

    suffix = ".exe" if sys.platform == "win32" else ""
    local_path = BIN_DIR / f"cloudflared{suffix}"
    if local_path.exists():
        return local_path

    logger.info("cloudflared not found, downloading...")
    _download_cloudflared(local_path)
    return local_path


def _download_cloudflared(dest: Path) -> None:
    """Download the cloudflared binary for the current platform to *dest*."""
    key = _platform_key()
    url = _DOWNLOAD_URLS.get(key)
    if not url:
        raise RuntimeError(
            f"No cloudflared binary for {key[0]}/{key[1]}. "
            "Install manually: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading cloudflared from %s", url)

    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()

    if url.endswith(".tgz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("cloudflared"):
                    f = tar.extractfile(member)
                    if f:
                        dest.write_bytes(f.read())
                        break
            else:
                raise RuntimeError("cloudflared binary not found in archive")
    else:
        dest.write_bytes(data)

    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    logger.info("cloudflared installed at %s", dest)


def _pipe_output(stream) -> None:
    """Read cloudflared output line-by-line and log tunnel/error events."""
    for line in stream:
        line = line.rstrip()
        if (
            "https://" in line and ".trycloudflare.com" in line
        ) or "Registered tunnel connection" in line:
            logger.info(line)
        elif "ERR" in line or "error" in line.lower():
            logger.error(line)


def start_tunnel(token: str, port: int) -> subprocess.Popen:
    """Launch a cloudflared tunnel process proxying to ``localhost:port``."""
    cloudflared = _get_cloudflared_path()
    cmd = [
        str(cloudflared),
        "tunnel",
        "--url",
        f"http://localhost:{port}",
        "run",
        "--token",
        token,
    ]
    logger.info("Starting cloudflared tunnel (proxying to localhost:%d)", port)

    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP", "USER", "LANG")
    }

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    threading.Thread(target=_pipe_output, args=(proc.stderr,), daemon=True).start()
    threading.Thread(target=_pipe_output, args=(proc.stdout,), daemon=True).start()

    logger.info("cloudflared tunnel started (pid=%d)", proc.pid)
    return proc


def stop_tunnel(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    logger.info("Stopping cloudflared tunnel")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("cloudflared did not stop gracefully, killing")
        proc.kill()
        proc.wait(timeout=5)
    logger.info("cloudflared tunnel stopped")
