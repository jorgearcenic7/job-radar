import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import update_source_catalog


STALE_DOCUMENT = """Header
<!-- BEGIN GENERATED SOURCE CATALOG -->
stale
<!-- END GENERATED SOURCE CATALOG -->
Footer
"""


class SourceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        (self.root / "docs").mkdir()

        for relative_path in update_source_catalog.DOCUMENTS:
            (self.root / relative_path).write_text(
                STALE_DOCUMENT,
                encoding="utf-8",
            )

    def test_check_detects_stale_documents_without_modifying_them(self):
        before = {
            path: (self.root / path).read_bytes()
            for path in update_source_catalog.DOCUMENTS
        }

        with redirect_stderr(io.StringIO()):
            result = update_source_catalog.main(["--check"], root=self.root)

        self.assertEqual(result, 1)
        self.assertEqual(
            before,
            {
                path: (self.root / path).read_bytes()
                for path in update_source_catalog.DOCUMENTS
            },
        )

    def test_update_is_idempotent_and_passes_check(self):
        with redirect_stdout(io.StringIO()):
            result = update_source_catalog.main([], root=self.root)

        self.assertEqual(result, 0)
        after_update = {
            path: (self.root / path).read_bytes()
            for path in update_source_catalog.DOCUMENTS
        }

        with redirect_stdout(io.StringIO()):
            result = update_source_catalog.main(["--check"], root=self.root)

        self.assertEqual(result, 0)
        self.assertEqual(
            after_update,
            {
                path: (self.root / path).read_bytes()
                for path in update_source_catalog.DOCUMENTS
            },
        )


if __name__ == "__main__":
    unittest.main()
