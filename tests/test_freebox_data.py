import unittest
from unittest.mock import patch, MagicMock

import freebox_data
from freebox_api import AuthRequired


def _resp(payload):
    return MagicMock(json=lambda: payload)


class TestFetchPorts(unittest.TestCase):
    @patch("freebox_data.requests.get")
    def test_skips_down_links_and_maps_speed(self, mock_get):
        def side_effect(url, headers=None):
            if url.endswith("/switch/status/"):
                return _resp({"success": True, "result": [
                    {"id": 1, "link": "up", "speed": "2500"},
                    {"id": 2, "link": "down", "speed": "1000"},
                    {"id": 9999, "link": "up", "speed": "10000"},
                ]})
            if url.endswith("/switch/port/1/stats/"):
                return _resp({"success": True, "result": {"rx_bytes_rate": 100, "tx_bytes_rate": 10}})
            if url.endswith("/switch/port/9999/stats/"):
                return _resp({"success": True, "result": {"rx_bytes_rate": -1, "tx_bytes_rate": -1}})
            raise AssertionError(f"unexpected URL {url}")

        mock_get.side_effect = side_effect

        ports = freebox_data.fetch_ports("session-token")

        self.assertEqual(ports, [
            {"port": "1", "speed": "2.5G", "down_bps": 800.0, "up_bps": 80.0},
            {"port": "SFP+", "speed": "10G", "down_bps": 0.0, "up_bps": 0.0},
        ])


class TestFetchDevices(unittest.TestCase):
    @patch("freebox_data.requests.get")
    def test_filters_and_sorts_by_ipv4(self, mock_get):
        mock_get.return_value = _resp({"result": [
            {
                "primary_name": "B",
                "host_type": "smartphone",
                "vendor_name": "Acme",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.20", "active": True},
                    {"af": "ipv6", "addr": "fe80::2", "active": True},
                ],
            },
            {
                "primary_name": "A",
                "host_type": "workstation",
                "vendor_name": "Acme",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.5", "active": True},
                ],
            },
            {
                "primary_name": "NoIP",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.99", "active": False},
                ],
            },
        ]})

        devices = freebox_data.fetch_devices("session-token")

        self.assertEqual([d["name"] for d in devices], ["A", "B"])
        self.assertEqual(devices[0]["ipv4"], "192.168.1.5")
        self.assertEqual(devices[1]["ipv6"], "fe80::2")


class TestFetchSnapshot(unittest.TestCase):
    @patch("freebox_data.fetch_devices", return_value=[])
    @patch("freebox_data.fetch_ports", return_value=[])
    @patch("freebox_data.requests.get")
    def test_raises_auth_required(self, mock_get, mock_ports, mock_devices):
        mock_get.return_value = _resp({"success": False, "error_code": "auth_required"})

        with self.assertRaises(AuthRequired):
            freebox_data.fetch_snapshot("session-token")

    @patch("freebox_data.fetch_devices", return_value=[{"name": "A"}])
    @patch("freebox_data.fetch_ports", return_value=[{"port": "1"}])
    @patch("freebox_data.requests.get")
    def test_builds_full_snapshot(self, mock_get, mock_ports, mock_devices):
        def side_effect(url, headers=None):
            if url.endswith("/connection/"):
                return _resp({"success": True, "result": {
                    "state": "up", "ipv4": "1.2.3.4", "ipv6": "::1",
                    "rate_down": 100, "rate_up": 10,
                }})
            if url.endswith("/system/"):
                return _resp({"result": {
                    "model_info": {"pretty_name": "Freebox v9 (r1)"},
                    "firmware_version": "4.12.2",
                    "box_authenticated": True,
                    "uptime": "1 jour",
                    "sensors": [{"id": "temp_cpu0", "name": "CPU 0", "value": 55}],
                    "fans": [{"id": "fan0_speed", "name": "Ventilateur 1", "value": 800}],
                }})
            if url.endswith("/phone/"):
                return _resp({"result": [{"hardware_defect": False}]})
            raise AssertionError(f"unexpected URL {url}")

        mock_get.side_effect = side_effect

        snap = freebox_data.fetch_snapshot("session-token")

        self.assertEqual(snap["model"], "Freebox v9 (r1)")
        self.assertEqual(snap["firmware"], "4.12.2")
        self.assertTrue(snap["internet_ok"])
        self.assertTrue(snap["auth_ok"])
        self.assertTrue(snap["phone_ok"])
        self.assertEqual(snap["wan_down_bps"], 800.0)
        self.assertEqual(snap["wan_up_bps"], 80.0)
        self.assertEqual(snap["ports"], [{"port": "1"}])
        self.assertEqual(snap["devices"], [{"name": "A"}])


if __name__ == "__main__":
    unittest.main()
