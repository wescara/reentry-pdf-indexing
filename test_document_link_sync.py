import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import repair_reentry_document_links as repair


class DocumentLinkSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "index.csv"
        self.original = (
            "filename,parent_category,web_url,file_id,extracted_text\n"
            "Document.pdf,Housing,https://example.com/direct,item-id,keep this text\n"
            "Other.pdf,Employment,https://example.com/other,other-id,other text\n"
        )
        self.path.write_text(self.original, encoding="utf-8")
        self.patcher = patch.object(repair, "SEARCH_INDEX_FULL", str(self.path))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.url = "https://example.sharepoint.com/:b:/s/site/anonymous"
        self.record = {
            "id": "knack-id",
            repair.FIELD_FILENAME: "Document.pdf",
            repair.FIELD_CATEGORY: "Housing",
            f"{repair.FIELD_URL}_raw": {"url": self.url},
        }

    def test_sync_existing_repaired_url_preserves_other_fields(self):
        self.assertEqual(repair.sync_csv_urls([self.record]), 1)
        with self.path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["web_url"], self.url)
        self.assertEqual(rows[0]["file_id"], "item-id")
        self.assertEqual(rows[0]["extracted_text"], "keep this text")
        self.assertEqual(rows[1]["web_url"], "https://example.com/other")
        before = self.path.read_bytes()
        self.assertEqual(repair.sync_csv_urls([self.record]), 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_dry_run_does_not_write(self):
        before = self.path.read_bytes()
        self.assertEqual(repair.sync_csv_urls([self.record], dry_run=True), 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_ambiguous_live_match_fails_without_write(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            repair.sync_csv_urls([self.record, dict(self.record)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_ambiguous_local_match_fails_without_write(self):
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("Document.pdf,Housing,https://example.com/direct,second-id,duplicate\n")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            repair.sync_csv_urls([self.record])
        self.assertEqual(self.path.read_bytes(), before)

    def test_nonanonymous_live_url_does_not_overwrite(self):
        self.record[f"{repair.FIELD_URL}_raw"]["url"] = "https://example.com/direct"
        before = self.path.read_bytes()
        with self.assertLogs(repair.logger, level="WARNING"):
            self.assertEqual(repair.sync_csv_urls([self.record]), 0)
        self.assertEqual(self.path.read_bytes(), before)

    @patch.object(repair.os, "replace", side_effect=OSError("write failed"))
    def test_atomic_replace_failure_preserves_original(self, replace):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(OSError, "write failed"):
            repair.sync_csv_urls([self.record])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_repair_syncs_when_all_live_links_already_anonymous(self):
        with (
            patch.object(repair, "get_graph_headers", return_value={}),
            patch.object(repair, "resolve_site_id", return_value="site"),
            patch.object(repair, "get_knack_records", return_value=[self.record]),
            patch.object(repair, "update_knack_url") as update,
        ):
            summary = repair.repair_links()
        update.assert_not_called()
        self.assertEqual(summary["to_fix"], 0)
        self.assertEqual(summary["csv_updated"], 1)

    def test_new_repair_is_synced_only_after_successful_knack_update(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.path.write_text(self.original, encoding="utf-8")
                self.record[f"{repair.FIELD_URL}_raw"]["url"] = "https://example.com/direct"
                with (
                    patch.object(repair, "get_graph_headers", return_value={}),
                    patch.object(repair, "resolve_site_id", return_value="site"),
                    patch.object(repair, "get_knack_records", return_value=[self.record]),
                    patch.object(repair, "create_anonymous_link_from_file_id", return_value=self.url),
                    patch.object(repair.time, "sleep"),
                    patch.object(
                        repair, "update_knack_url",
                        side_effect=RuntimeError("Knack failed") if fail else None,
                    ) as update,
                ):
                    summary = repair.repair_links()
                update.assert_called_once_with("knack-id", self.url)
                self.assertEqual(summary["failed"], int(fail))
                self.assertEqual(summary["csv_updated"], int(not fail))
                with self.path.open(newline="", encoding="utf-8") as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual(row["web_url"], "https://example.com/direct" if fail else self.url)


if __name__ == "__main__":
    unittest.main()
