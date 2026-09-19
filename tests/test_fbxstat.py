import unittest

import fbxstat


class TestFmtSize(unittest.TestCase):
    def test_scales_automatically(self):
        self.assertEqual(fbxstat.fmt_size(512), "512 o")
        self.assertEqual(fbxstat.fmt_size(1_500), "1.50 Ko")
        self.assertEqual(fbxstat.fmt_size(39_048_044), "39.05 Mo")
        self.assertEqual(fbxstat.fmt_size(10_304_026_303), "10.30 Go")
        self.assertEqual(fbxstat.fmt_size(2_840_059_594_048), "2.84 To")

    def test_negative_is_clamped_to_zero(self):
        self.assertEqual(fbxstat.fmt_size(-1), "0 o")


if __name__ == "__main__":
    unittest.main()
