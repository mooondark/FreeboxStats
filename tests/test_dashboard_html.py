import os
import re
import unittest

HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "templates", "dashboard.html")
with open(HTML_PATH, encoding="utf-8") as f:
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


class TestResponsive(unittest.TestCase):
    def test_has_viewport_meta(self):
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', HTML)

    def test_tables_scroll_horizontally_on_narrow_screens(self):
        self.assertEqual(HTML.count('<div class="table-wrap">'), 2)

    def test_charts_have_a_sized_container(self):
        self.assertEqual(HTML.count('<div class="chart-box">'), 2)
        self.assertEqual(HTML.count("maintainAspectRatio: false"), 2)

    def test_keep_awake_checkbox_is_present_and_checked_by_default(self):
        self.assertIn('id="keep-awake"', HTML)
        checkbox = re.search(r'<input[^>]*id="keep-awake"[^>]*>', HTML).group(0)
        self.assertIn("checked", checkbox)
        self.assertIn("navigator.wakeLock", HTML)


if __name__ == "__main__":
    unittest.main()
