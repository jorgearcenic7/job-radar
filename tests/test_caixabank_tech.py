import unittest
from unittest.mock import patch

import main


class CaixaBankTechTests(unittest.TestCase):
    @patch("main.fetch_text")
    def test_listing_and_details(self, fetch_text):
        listing = (
            '<a href="/es/job/data-engineer/">'
            'Data Engineer</a>'
        )

        detail = (
            "<title>CaixaBank Tech | Data Engineer</title>"
            "<p>Ubicación: Barcelona Madrid</p>"
            "<p>Jornada Laboral: Tiempo completo</p>"
            "<p>Contrato: Indefinido</p>"
            "<p>Buscamos personas curiosas.</p>"
            "<p>2 years of experience building data pipelines.</p>"
        )

        fetch_text.side_effect = [listing, detail]

        jobs = main.fetch_caixabank_tech()

        self.assertEqual(len(jobs), 1)

        job = jobs[0]

        self.assertEqual(job["title"], "Data Engineer")
        self.assertEqual(
            job["source"],
            "caixabank-tech:careers",
        )
        self.assertEqual(
            job["source_job_id"],
            "data-engineer",
        )
        self.assertEqual(
            job["location"],
            "Barcelona; Madrid",
        )
        self.assertEqual(
            job["experience_text"],
            "2 years of experience",
        )

    @patch("main.fetch_text")
    def test_empty_listing_is_rejected(self, fetch_text):
        fetch_text.return_value = "<html>No vacancies</html>"

        with self.assertRaisesRegex(
            ValueError,
            "no job links found",
        ):
            main.fetch_caixabank_tech()


if __name__ == "__main__":
    unittest.main()
