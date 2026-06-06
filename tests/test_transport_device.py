"""Tests for transport/protocol device-config parsing."""

from mllp_gateway.transport.device import (
    DeviceConfig,
    parse_device_config,
    parse_device_configs,
)


def test_ethernet_hl7_defaults():
    cfg = parse_device_config({"id": "d1", "endpoint_address": "10.0.0.5"})
    assert cfg.transport == "ethernet"
    assert cfg.protocol == "hl7"
    assert cfg.connection_key == "10.0.0.5"
    assert cfg.is_serial is False
    assert cfg.is_astm is False
    assert cfg.serial is None


def test_serial_astm_parsing():
    cfg = parse_device_config(
        {
            "id": "d2",
            "transport": "serial",
            "protocol": "astm",
            "serial_port": "/dev/ttyUSB0",
            "baud_rate": 19200,
            "parity": "E",
            "stop_bits": 2,
            "flow_control": "rtscts",
        }
    )
    assert cfg.is_serial is True
    assert cfg.is_astm is True
    # Serial devices are tracked by id (no IP).
    assert cfg.connection_key == "d2"
    assert cfg.serial is not None
    assert cfg.serial.baud_rate == 19200
    kwargs = cfg.serial.to_pyserial_kwargs()
    assert kwargs["url"] == "/dev/ttyUSB0"
    assert kwargs["baudrate"] == 19200
    assert kwargs["parity"] == "E"
    assert kwargs["rtscts"] is True
    assert kwargs["xonxoff"] is False


def test_unknown_transport_protocol_fall_back():
    cfg = parse_device_config(
        {"id": "d3", "transport": "carrier-pigeon", "protocol": "smoke-signal"}
    )
    assert cfg.transport == "ethernet"
    assert cfg.protocol == "hl7"


def test_malformed_entries_are_skipped():
    configs = parse_device_configs(
        [
            {"id": "ok", "endpoint_address": "1.2.3.4"},
            None,  # malformed — should be skipped, not raise
        ]
    )
    assert [c.id for c in configs] == ["ok"]


def test_connection_key_prefers_id_when_no_ip():
    cfg = DeviceConfig(
        id="x", registered_name="n", transport="ethernet", protocol="hl7"
    )
    assert cfg.connection_key == "x"
