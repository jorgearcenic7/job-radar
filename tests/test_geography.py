import unittest

from job_radar.matching import infer_countries


class GeographyTests(unittest.TestCase):
    def assert_countries(self, cases):
        for location, expected in cases:
            with self.subTest(location=location):
                self.assertEqual(infer_countries(location), expected)

    def test_infer_countries_initializes_geography_cache(self):
        self.assertEqual(infer_countries("Madrid, Spain"), ["Spain"])

    def test_us_state_codes_are_not_read_as_countries(self):
        # Casos reales de la web: GA era Gabón, IL Israel, PA Panamá…
        self.assert_countries([
            ("Atlanta, GA, United States of America", ["United States"]),
            ("Atlanta, GA", ["United States"]),
            ("Chicago, IL; Atlanta, GA", ["United States"]),
            ("Baltimore, MD, United States of America", ["United States"]),
            ("Charlotte, NC", ["United States"]),
            ("Saint Louis, MO", ["United States"]),
            ("Birmingham, AL", ["United States"]),
        ])

    def test_country_codes_next_to_their_cities_are_kept(self):
        self.assert_countries([
            ("Madrid, ES", ["Spain"]),
            ("Valencia, ES", ["Spain"]),
            ("Remote (ES)", ["Spain"]),
            ("Toronto, CA", ["Canada"]),
            ("Bogotá, CO", ["Colombia"]),
            ("Tel Aviv, IL", ["Israel"]),
            ("Berlin, DE", ["Germany"]),
            ("Panama City, PA", ["Panama"]),
        ])

    def test_us_state_names_are_not_countries(self):
        self.assert_countries([
            ("Dallas, Texas; New Jersey; New York", ["United States"]),
            ("Albuquerque, New Mexico", ["United States"]),
            ("Remote, US, Georgia", ["United States"]),
            ("Atlanta, Georgia", ["United States"]),
            ("Florida", ["United States"]),
            ("Tbilisi, Georgia", ["Georgia"]),
        ])

    def test_explicit_country_wins_over_regional_codes(self):
        self.assert_countries([
            ("India, MH, Pune", ["India"]),
            ("China, SH, Shanghai", ["China"]),
            ("Vitória, ES, Brazil", ["Brazil"]),
            ("Frosinone, FR, Italy", ["Italy"]),
            ("US / Canada", ["Canada", "United States"]),
            ("UK / Ireland", ["Ireland", "United Kingdom"]),
        ])

    def test_longest_country_name_wins(self):
        self.assert_countries([
            ("Port Moresby, Papua New Guinea", ["Papua New Guinea"]),
            ("Juba, South Sudan", ["South Sudan"]),
            ("Belfast, Northern Ireland", ["United Kingdom"]),
            (
                "Sarajevo, Bosnia & Herzegovina",
                ["Bosnia and Herzegovina"],
            ),
        ])


if __name__ == "__main__":
    unittest.main()
