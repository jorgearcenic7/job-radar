import unittest

from job_radar.matching import infer_countries


class GeographyTests(unittest.TestCase):
    def test_infer_countries_initializes_geography_cache(self):
        self.assertEqual(
            infer_countries("Madrid, Spain"),
            ["Spain"],
        )


if __name__ == "__main__":
    unittest.main()
