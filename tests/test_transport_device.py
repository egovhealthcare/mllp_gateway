"""Tests for transport/protocol device-config parsing."""

import unittest

from mllp_gateway.transport.device import (
    DeviceConfig,
    parse_device_config,
    parse_device_configs,
)


class TestDeviceConfigParsing(unittest.TestCase):

    def test_ethernet_hl7_defaults(self):
        cfg = parse_device_config({"id": "d1", "endpoint_address": "10.0.0.5"})
        self.assertEqual(cfg.transport, "ethernet")
        self.assertEqual(cfg.protocol, "hl7")
        self.assertEqual(cfg.connection_key, "10.0.0.5")
        self.assertFalse(cfg.is_serial)
        self.assertFalse(cfg.is_astm)
        self.assertIsNone(cfg.serial)

    def test_serial_astm_parsing(self):
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
        self.assertTrue(cfg.is_serial)
        self.assertTrue(cfg.is_astm)
        self.assertEqual(cfg.connection_key, "d2")
        self.assertIsNotNone(cfg.serial)
        self.assertEqual(cfg.serial.baud_rate, 19200)
        kwargs = cfg.serial.to_pyserial_kwargs()
        self.assertEqual(kwargs["url"], "/dev/ttyUSB0")
        self.assertEqual(kwargs["baudrate"], 19200)
        self.assertEqual(kwargs["parity"], "E")
        self.assertTrue(kwargs["rtscts"])
        self.assertFalse(kwargs["xonxoff"])

    def test_unknown_transport_protocol_fall_back(self):
        cfg = parse_device_config(
            {"id": "d3", "transport": "carrier-pigeon", "protocol": "smoke-signal"}
        )
        self.assertEqual(cfg.transport, "ethernet")
        self.assertEqual(cfg.protocol, "hl7")

    def test_malformed_entries_are_skipped(self):
        configs = parse_device_configs(
            [
                {"id": "ok", "endpoint_address": "1.2.3.4"},
                None,
            ]
        )
        self.assertEqual([c.id for c in configs], ["ok"])

    def test_connection_key_prefers_id_when_no_ip(self):
        cfg = DeviceConfig(
            id="x", registered_name="n", transport="ethernet", protocol="hl7"
        )
        self.assertEqual(cfg.connection_key, "x")

    def test_mindray_bc_5150_outbound_from_care_payload(self):
        """Gateway uses CARE-provided values; device profiles default in CARE."""
        cfg = parse_device_config(
            {
                "id": "d-mindray",
                "type": "mindray_bc_5150",
                "endpoint_address": "192.168.1.50",
                "oru_port": 5100,
                "hl7_connection_mode": "outbound",
            }
        )
        self.assertEqual(cfg.hl7_connection_mode, "outbound")
        self.assertEqual(cfg.oru_port, 5100)
        self.assertTrue(cfg.is_outbound_hl7)

    def test_explicit_hl7_connection_mode_override(self):
        cfg = parse_device_config(
            {
                "id": "d1",
                "type": "generic",
                "endpoint_address": "10.0.0.5",
                "hl7_connection_mode": "outbound",
            }
        )
        self.assertEqual(cfg.hl7_connection_mode, "outbound")
        self.assertTrue(cfg.is_outbound_hl7)


if __name__ == "__main__":
    unittest.main()
