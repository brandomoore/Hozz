import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from receiver.hozz_receiver import connect, ingest_file


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "beta_demo", ROOT / "tools/create-beta-demo.py",
)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class BetaDemoTests(unittest.TestCase):
    def test_deterministic_archive_with_only_archive_only_types(self):
        mappings = json.loads(
            (ROOT / "schema/hozz/v1/health-connect-mappings.json").read_text()
        )
        archive_only = {
            entry["canonicalType"]
            for entry in mappings["recordMappings"]
            if entry["quality"] == "archiveOnly"
        }
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.zip"
            second = Path(temporary) / "second.zip"
            manifest = demo.write_demo(first)
            demo.write_demo(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(manifest["recordCount"], 242)
            with zipfile.ZipFile(first) as archive:
                rows = [
                    json.loads(line)
                    for line in archive.read(manifest["recordsEntry"]).splitlines()
                ]
            self.assertEqual(len(rows), 242)
            self.assertEqual(len({row["canonicalId"] for row in rows}), 242)
            self.assertTrue(all(row["canonicalType"] in archive_only for row in rows))
            self.assertEqual(sum(row["kind"] == "deletion" for row in rows), 1)

    def test_strict_import_retry_retains_live_records_and_tombstone(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hozz-beta-demo.zip"
            demo.write_demo(path)
            database = connect(Path(temporary) / "receiver.sqlite")
            try:
                ingest_file(database, path)
                before = database.execute(
                    "SELECT canonical_id, raw, tombstone FROM samples ORDER BY canonical_id"
                ).fetchall()
                self.assertEqual(len(before), 242)
                self.assertEqual(sum(row[2] for row in before), 1)
                ingest_file(database, path)
                after = database.execute(
                    "SELECT canonical_id, raw, tombstone FROM samples ORDER BY canonical_id"
                ).fetchall()
                self.assertEqual(before, after)
            finally:
                database.close()

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "existing.zip"
            path.write_bytes(b"keep existing file")
            with self.assertRaises(FileExistsError):
                demo.write_demo(path)
            self.assertEqual(path.read_bytes(), b"keep existing file")


if __name__ == "__main__":
    unittest.main()
