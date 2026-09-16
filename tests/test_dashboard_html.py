import unittest

with open("templates/dashboard.html", encoding="utf-8") as f:
    HTML = f.read()


class TestDashboardHtml(unittest.TestCase):
    def test_has_chartjs_cdn_script(self):
        self.assertIn("cdnjs.cloudflare.com/ajax/libs/Chart.js", HTML)

    def test_has_both_chart_canvases(self):
        self.assertIn('id="wan-chart"', HTML)
        self.assertIn('id="temp-chart"', HTML)

    def test_has_both_table_bodies(self):
        self.assertIn('id="ports-body"', HTML)
        self.assertIn('id="devices-body"', HTML)

    def test_has_status_header_elements(self):
        for el_id in ["hdr-model", "hdr-firmware", "hdr-internet", "hdr-auth", "hdr-phone", "hdr-ipv4", "hdr-ipv6", "hdr-uptime"]:
            self.assertIn(f'id="{el_id}"', HTML)


if __name__ == "__main__":
    unittest.main()
