"""Typed device configuration parsed from CARE's configured-devices payload.

The gateway fetches a list of devices from CARE (see
:meth:`mllp_gateway.care.CareClient.fetch_configured_devices`). Historically
each device was an HL7-over-TCP analyzer identified by ``endpoint_address``.

To support RS232 and ASTM, each device now also carries a ``transport`` and a
``protocol`` selector plus serial-line settings. This module normalises the
raw dicts into a typed :class:`DeviceConfig` and computes a stable
``connection_key`` used to track the device's live connection regardless of
transport (TCP devices have an IP, serial devices do not).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

Transport = Literal["ethernet", "serial"]
Protocol = Literal["hl7", "astm"]

_VALID_TRANSPORTS: frozenset[str] = frozenset({"ethernet", "serial"})
_VALID_PROTOCOLS: frozenset[str] = frozenset({"hl7", "astm"})

# pyserial parity/stopbit string constants accepted from CARE config.
_PARITY_MAP = {"N": "N", "E": "E", "O": "O", "M": "M", "S": "S"}
_FLOW_CONTROL = frozenset({"none", "xonxoff", "rtscts"})


@dataclass(frozen=True)
class SerialSettings:
    """RS232 line settings for a serial-attached analyzer."""

    port: str  # e.g. "/dev/ttyUSB0" or "COM3"
    baud_rate: int = 9600
    data_bits: int = 8
    parity: str = "N"  # N, E, O, M, S
    stop_bits: float = 1  # 1, 1.5, 2
    flow_control: str = "none"  # none, xonxoff, rtscts

    def to_pyserial_kwargs(self) -> dict[str, Any]:
        """Translate to keyword arguments for ``serial_asyncio``/pyserial."""
        return {
            "url": self.port,
            "baudrate": self.baud_rate,
            "bytesize": self.data_bits,
            "parity": self.parity,
            "stopbits": self.stop_bits,
            "xonxoff": self.flow_control == "xonxoff",
            "rtscts": self.flow_control == "rtscts",
        }


@dataclass(frozen=True)
class DeviceConfig:
    """Normalised configuration for a single analyzer.

    ``connection_key`` is the stable identifier used to track the device's
    live connection and to tag forwarded messages. For Ethernet devices it is
    the ``endpoint_address`` (IP); for serial devices it is the device's CARE
    id, which is also sent to CARE so it can resolve the source device.
    """

    id: str
    registered_name: str
    transport: Transport
    protocol: Protocol
    type: str = "generic"
    # Ethernet / HL7 fields
    endpoint_address: str | None = None
    oru_port: int = 2575
    orm_port: int | None = None
    orm_mode: str = "shared"
    # Serial fields
    serial: SerialSettings | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def connection_key(self) -> str:
        """Stable key for connection tracking and message tagging."""
        if self.transport == "ethernet" and self.endpoint_address:
            return self.endpoint_address
        return self.id

    @property
    def is_serial(self) -> bool:
        return self.transport == "serial"

    @property
    def is_astm(self) -> bool:
        return self.protocol == "astm"


def _coerce_transport(value: Any) -> Transport:
    text = str(value or "ethernet").lower()
    if text not in _VALID_TRANSPORTS:
        logger.warning("Unknown transport %r, defaulting to 'ethernet'", value)
        return "ethernet"
    return cast(Transport, text)


def _coerce_protocol(value: Any) -> Protocol:
    text = str(value or "hl7").lower()
    if text not in _VALID_PROTOCOLS:
        logger.warning("Unknown protocol %r, defaulting to 'hl7'", value)
        return "hl7"
    return cast(Protocol, text)


def _parse_serial(raw: dict[str, Any]) -> SerialSettings | None:
    port = raw.get("serial_port")
    if not port:
        return None
    parity = str(raw.get("parity", "N")).upper()
    if parity not in _PARITY_MAP:
        parity = "N"
    flow = str(raw.get("flow_control", "none")).lower()
    if flow not in _FLOW_CONTROL:
        flow = "none"
    try:
        stop_bits = float(raw.get("stop_bits", 1))
    except (TypeError, ValueError):
        stop_bits = 1.0
    return SerialSettings(
        port=str(port),
        baud_rate=int(raw.get("baud_rate", 9600)),
        data_bits=int(raw.get("data_bits", 8)),
        parity=parity,
        stop_bits=stop_bits,
        flow_control=flow,
    )


def parse_device_config(raw: dict[str, Any]) -> DeviceConfig:
    """Build a :class:`DeviceConfig` from a raw CARE device dict.

    Unknown or missing fields fall back to safe defaults so that older CARE
    deployments (which only send Ethernet/HL7 fields) keep working.
    """
    transport = _coerce_transport(raw.get("transport"))
    protocol = _coerce_protocol(raw.get("protocol"))
    orm_port = raw.get("orm_port")
    return DeviceConfig(
        id=str(raw.get("id", "")),
        registered_name=str(raw.get("registered_name", "")),
        transport=transport,
        protocol=protocol,
        type=str(raw.get("type", "generic")),
        endpoint_address=raw.get("endpoint_address"),
        oru_port=int(raw.get("oru_port", 2575) or 2575),
        orm_port=int(orm_port) if orm_port else None,
        orm_mode=str(raw.get("orm_mode", "shared")),
        serial=_parse_serial(raw) if transport == "serial" else None,
        raw=raw,
    )


def parse_device_configs(raw_devices: list[dict[str, Any]]) -> list[DeviceConfig]:
    """Parse a list of raw CARE device dicts, skipping malformed entries."""
    configs: list[DeviceConfig] = []
    for raw in raw_devices:
        try:
            configs.append(parse_device_config(raw))
        except Exception as e:  # noqa: BLE001 — defensive: one bad device shouldn't kill startup
            logger.error("Skipping malformed device config %r: %s", raw, e)
    return configs
