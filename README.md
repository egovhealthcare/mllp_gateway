# MLLP Gateway

Standalone MLLP gateway for lab analyzer HL7 communication with [CARE](https://github.com/ohcnetwork/care).

Receives HL7 ORU (results) and ORM (orders) messages over MLLP from lab analyzers, and forwards them to the CARE backend. Runs as a background service with a system tray icon showing live connection status.

## Download

Download the latest binary for your platform from [Releases](https://github.com/egovhealthcare/mllp_gateway/releases):

| Platform | File |
|----------|------|
| Linux x86_64 | `mllp-gateway-linux-amd64` |
| macOS Apple Silicon | `mllp-gateway-darwin-arm64` |
| Windows x86_64 | `mllp-gateway-windows-amd64.exe` |

No Python installation required — the binary is fully self-contained.

## Installation

### Pre-built Binary (recommended for desktops)

Download from [Releases](https://github.com/egovhealthcare/mllp_gateway/releases), make it executable, and move it to your PATH:

```bash
# Linux / macOS
chmod +x mllp-gateway-*
sudo mv mllp-gateway-* /usr/local/bin/mllp-gateway

# Windows — move the .exe to a folder in your PATH
```

The system tray icon requires a display server at runtime (X11/Wayland on Linux, GUI session on macOS/Windows). On headless servers it automatically falls back to headless mode.

### From Source

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/egovhealthcare/mllp_gateway.git
cd mllp_gateway
uv sync
uv run mllp-gateway
```

## First Run

Run the binary (double-click or from a terminal). On the first run, you'll be prompted to configure:

```
MLLP Gateway — Configuration

  CARE API URL (e.g. https://care.example.com): https://care.example.com
  Gateway Device ID: abc123-def456
  Cloudflare Tunnel token (optional) []:
  ORU MLLP port [2575]:
  ORM MLLP port [2576]:
  HTTP API port [8090]:
  Web UI port (localhost only) [8080]:
  Message retention days [14]:

Configuration saved to ~/.mllp_gateway/config.toml
```

To re-run the setup wizard later (existing values are shown as defaults):

```bash
mllp-gateway configure
```

## Install as Service

Register the gateway to start automatically on boot:

```bash
mllp-gateway install
```

This creates a user-level service (no root/admin required):
- **Linux**: systemd user unit (`~/.config/systemd/user/mllp-gateway.service`)
- **macOS**: launchd agent (`~/Library/LaunchAgents/network.ohc.mllp-gateway.plist`)
- **Windows**: scheduled task at logon

### System-wide service (Linux headless servers)

For headless servers where no user session is guaranteed, install as a system-level systemd unit:

```bash
sudo mllp-gateway install --system
```

This creates `/etc/systemd/system/mllp-gateway.service` running as the current user with `--no-tray`. It requires root and uses `WantedBy=multi-user.target`.

### Other service commands

```bash
mllp-gateway status              # Check user service status
mllp-gateway status --system     # Check system service status
mllp-gateway uninstall           # Remove user service
mllp-gateway uninstall --system  # Remove system service (requires root)
```

## System Tray

When run without `--no-tray`, a system tray icon appears showing:

- **Green dot**: Running normally
- **Yellow dot**: Degraded (tunnel disconnected or device issues)
- **Red dot**: Error state

Right-click menu:
- Gateway version and status
- Open Web UI
- Update info (if available)
- CARE API connection state
- Tunnel connection state
- Number of connected devices
- Restart / Open Config / Exit

## Configuration

Configuration is stored at `~/.mllp_gateway/config.toml`:

```toml
[gateway]
care_api_url = "https://care.example.com"
device_id = "your-gateway-device-id"
tunnel_token = ""

[ports]
oru = 2575
orm = 2576
api = 8090
ui = 8080

[storage]
retention_days = 14

[updates]
auto_update = true
check_interval_hours = 6
github_repo = "egovhealthcare/mllp_gateway"
```

### Signing Key

If neither `SIGNING_KEY` nor `JWKS_BASE64` is set, an RSA-2048 key is auto-generated and stored at `~/.mllp_gateway/key.pem`. This key is used to authenticate with the CARE backend via JWT.

## Running Headless

For servers without a display (or when running as a service):

```bash
mllp-gateway run --no-tray
```

Logs are written to stderr and to `~/.mllp_gateway/gateway.log` (rotated at 5 MB, 3 backups). Stored messages are automatically purged after the configured retention period (default 14 days).

## Web UI

A local web dashboard is available at `http://127.0.0.1:8080` (localhost only, no authentication required):

- Real-time message list (sent and received) via WebSocket
- Statistics: received, sent, forwarded counts, connected devices
- Per-device connection status and last activity
- Manual order sending form for testing
- Individual message detail view with full HL7 content and ACK response

The tray menu includes an "Open Web UI" item to launch it in your browser.

## CLI Reference

```
mllp-gateway                   # Check for updates, then ensure service is running
mllp-gateway run               # Start with system tray
mllp-gateway run --no-tray     # Start headless (no display required)
mllp-gateway configure         # Run configuration wizard
mllp-gateway install           # Install as user-level service
mllp-gateway install --system  # Install as system-wide systemd unit (Linux, root)
mllp-gateway uninstall         # Remove user-level service
mllp-gateway uninstall --system
mllp-gateway status            # Show user service status
mllp-gateway status --system   # Show system service status
mllp-gateway update --check    # Check for available updates
mllp-gateway update            # Download and apply an update
mllp-gateway update --force    # Apply even if it's a breaking major version
```

## Auto-Update

The gateway periodically checks GitHub Releases for new versions (every 6 hours by default). When running as a frozen binary:

- **Non-breaking updates** (same major version) are applied automatically if `auto_update` is enabled.
- **Breaking updates** (major version bump) require manual confirmation via `mllp-gateway update --force`.

If the tray is active, a notification appears when an update is available.

Configure via the `[updates]` section in `config.toml`.

When not running as a binary (e.g. from source), auto-update is disabled and you'll be directed to download from GitHub Releases.

## Building a Standalone Binary

To build a self-contained binary (used for releases):

```bash
uv sync --group dev
uv run pyinstaller mllp_gateway.spec --noconfirm
# Output: dist/mllp-gateway
```

The tray icon requires a display server at runtime but the binary itself can be built on headless CI.

## Architecture

```
Lab Analyzer                    MLLP Gateway                    CARE Backend
     |                               |                               |
     |--- ORU (results) :2575 -----> |                               |
     |                               |--- forward_result() --------> |
     |                               |                               |
     |<-- ORM (orders) via MLLP ---- |<-- POST /send-order --------- |
     |                               |                               |
     |                               | HTTP API (:8090)              |
     |                               |   POST /send-order            |
     |                               |   GET  /health                |
     |                               |   GET  /openid-configuration/ |
     |                               |                               |
     |                               | Web UI (:8080, localhost)     |
     |                               |   Dashboard + WebSocket       |
     |                               |                               |
     |                               |==== Cloudflare Tunnel ======> | (optional)
```

### ORM Delivery Modes

The `/send-order` endpoint supports three modes for delivering ORM messages to analyzers:

| Mode | Description |
|------|-------------|
| `shared` | Reuses the analyzer's existing ORU connection; falls back to `client` if unavailable |
| `server` | Uses a dedicated ORM connection initiated by the analyzer to the gateway's ORM port |
| `client` | Opens a new outbound MLLP connection to the analyzer |

### Cloudflare Tunnel

If a `tunnel_token` is configured, the gateway automatically downloads and runs `cloudflared` to expose the HTTP API through a Cloudflare Tunnel. The `cloudflared` binary is downloaded to `~/.mllp_gateway/bin/` if not already installed on the system.

## License

MIT