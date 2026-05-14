"""Gateway configuration: TOML loading, interactive setup, and key management."""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import tomllib
import tomli_w

__all__ = [
    "APP_DIR",
    "CONFIG_FILE",
    "Config",
    "LOG_FILE",
    "load_config",
    "run_configure",
]

logger = logging.getLogger(__name__)

APP_DIR = Path.home() / ".mllp_gateway"
CONFIG_FILE = APP_DIR / "config.toml"
KEY_FILE = APP_DIR / "key.pem"
LOG_FILE = APP_DIR / "gateway.log"


def _validate_port(value: int, name: str) -> int:
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be 1–65535, got {value}")
    return value


def _prompt_required(name: str, prompt: str) -> str:
    """Prompt interactively until a non-empty value is entered.

    Raises SystemExit on Ctrl-C / EOF, RuntimeError if stdin is not a TTY.
    """
    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(f"{name} is required. Run 'mllp-gateway configure'.")
    while True:
        print(f"  {prompt}", end="", flush=True)
        try:
            value = input().strip()
        except (KeyboardInterrupt, EOFError):
            print()
            raise SystemExit(1)
        if value:
            return value
        print(f"  {name} cannot be empty. Please try again.")


def _prompt_optional(prompt: str, default: str) -> str:
    """Prompt for a value with a default; returns *default* on non-TTY stdin."""
    if not sys.stdin.isatty():
        return default
    print(f"  {prompt} [{default}]: ", end="", flush=True)
    return input().strip() or default


def run_configure() -> Path:
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = tomllib.loads(CONFIG_FILE.read_text())
        except Exception:
            pass

    gw = existing.get("gateway", {})
    ports = existing.get("ports", {})

    print("MLLP Gateway — Configuration\n")

    care_api_url = (
        _prompt_optional("CARE API URL", gw["care_api_url"])
        if gw.get("care_api_url")
        else _prompt_required(
            "CARE_API_URL", "CARE API URL (e.g. https://care.example.com): "
        )
    )
    device_id = (
        _prompt_optional("Gateway Device ID", gw["device_id"])
        if gw.get("device_id")
        else _prompt_required("GATEWAY_DEVICE_ID", "Gateway Device ID: ")
    )
    tunnel_token = _prompt_optional(
        "Cloudflare Tunnel token (optional)", gw.get("tunnel_token", "")
    )
    oru_port = _prompt_optional("ORU MLLP port", str(ports.get("oru", 2575)))
    orm_port = _prompt_optional("ORM MLLP port", str(ports.get("orm", 2576)))
    api_port = _prompt_optional("HTTP API port", str(ports.get("api", 8090)))
    ui_port = _prompt_optional(
        "Web UI port (localhost only)", str(ports.get("ui", 8080))
    )

    storage = existing.get("storage", {})
    retention_days = _prompt_optional(
        "Message retention days", str(storage.get("retention_days", 14))
    )

    data = {
        "gateway": {
            "care_api_url": care_api_url.rstrip("/"),
            "device_id": device_id,
            "tunnel_token": tunnel_token,
        },
        "ports": {
            "oru": int(oru_port),
            "orm": int(orm_port),
            "api": int(api_port),
            "ui": int(ui_port),
        },
        "storage": {"retention_days": int(retention_days)},
    }

    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_bytes(tomli_w.dumps(data).encode())
    print(f"\nConfiguration saved to {CONFIG_FILE}")
    return CONFIG_FILE


def _load_private_key_pem() -> bytes:
    """Load or generate the gateway's RSA private key.

    If the key file doesn't exist, a new 2048-bit RSA key is generated and
    saved with restrictive permissions (0600 on Unix, best-effort on Windows).
    """
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    APP_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(pem)
    try:
        KEY_FILE.chmod(0o600)
    except OSError:
        logger.warning("Could not restrict key file permissions: %s", KEY_FILE)
    return pem


@dataclass
class Config:
    """Resolved runtime configuration for the gateway.

    Built from the TOML config file via :func:`load_config`. Ports are
    validated on construction. The private key PEM is excluded from repr
    for safety.
    """

    care_api_url: str
    gateway_device_id: str
    private_key_pem: bytes = field(repr=False)
    oru_port: int = 2575
    orm_port: int = 2576
    api_port: int = 8090
    api_host: str = "0.0.0.0"
    ui_port: int = 8080
    tunnel_token: str | None = None
    care_api_timeout: int = 25
    retention_days: int = 14
    disable_auth: bool = False
    auto_update: bool = True
    update_check_interval: int = 6
    github_repo: str = "egovhealthcare/mllp_gateway"


def load_config() -> Config:
    if not CONFIG_FILE.exists():
        run_configure()

    data = tomllib.loads(CONFIG_FILE.read_text())
    gw = data.get("gateway", {})
    ports = data.get("ports", {})
    updates = data.get("updates", {})
    storage = data.get("storage", {})

    care_api_url = gw.get("care_api_url", "")
    gateway_device_id = gw.get("device_id", "")

    if not care_api_url or not gateway_device_id:
        raise RuntimeError(
            "care_api_url and device_id are required. Run 'mllp-gateway configure'."
        )

    return Config(
        care_api_url=care_api_url.rstrip("/"),
        gateway_device_id=gateway_device_id,
        private_key_pem=_load_private_key_pem(),
        oru_port=_validate_port(ports.get("oru", 2575), "oru"),
        orm_port=_validate_port(ports.get("orm", 2576), "orm"),
        api_port=_validate_port(ports.get("api", 8090), "api"),
        api_host=gw.get("api_host", "0.0.0.0"),
        ui_port=_validate_port(ports.get("ui", 8080), "ui"),
        tunnel_token=gw.get("tunnel_token") or None,
        care_api_timeout=int(gw.get("care_api_timeout", 25)),
        retention_days=storage.get("retention_days", 14),
        disable_auth=gw.get("disable_auth", False),
        auto_update=updates.get("auto_update", True),
        update_check_interval=updates.get(
            "update_check_interval", updates.get("check_interval_hours", 6)
        ),
        github_repo=updates.get("github_repo", "egovhealthcare/mllp_gateway"),
    )
