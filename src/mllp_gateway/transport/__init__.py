"""Transport and protocol selection for lab analyzer communication.

Two orthogonal axes describe how the gateway talks to an analyzer:

- **Transport** — the byte stream: ``ethernet`` (TCP) or ``serial`` (RS232).
- **Protocol** — the framing + codec: ``hl7`` (MLLP) or ``astm`` (E1381).

All four combinations are supported: hl7+ethernet, hl7+serial,
astm+ethernet, astm+serial.
"""

from mllp_gateway.transport.device import (
    DeviceConfig,
    Protocol,
    SerialSettings,
    Transport,
)

__all__ = [
    "DeviceConfig",
    "Protocol",
    "SerialSettings",
    "Transport",
]
