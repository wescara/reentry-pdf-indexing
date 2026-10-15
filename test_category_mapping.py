import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

from build_search_index import extract_parent_category
from repair_reentry_category_links import get_folder_item_id


class CategoryMappingTests(unittest.TestCase):
    def test_courts_folder_uses_original_category(self):
        for folder in (
            "Courts (Navigating Court Cases)",
            "Courts (How to Navigate Court Cases)",
        ):
            with self.subTest(folder=folder):
                self.assertEqual(
                    extract_parent_category(f"/drive/root:/Oxbow Reentry/{folder}/Subfolder"),
                    "Courts (How to Navigate Court Cases)",
                )

    def test_other_category_is_preserved(self):
        self.assertEqual(
            extract_parent_category("/drive/root:/Oxbow Reentry/Education and Training"),
            "Education and Training",
        )

    def test_missing_category(self):
        self.assertIsNone(extract_parent_category(""))
        self.assertIsNone(extract_parent_category("/drive/root:/Oxbow Reentry"))

    def test_womens_apostrophe_mapping_is_preserved(self):
        self.assertEqual(
            extract_parent_category("/drive/root:/Oxbow Reentry/Women's Resources"),
            "Women\u2019s Resources",
        )

    @patch("repair_reentry_category_links.requests.get")
    def test_original_courts_category_resolves_actual_folder(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"id": "folder-id"}
        self.assertEqual(
            get_folder_item_id("site-id", "Courts (How to Navigate Court Cases)", {}),
            "folder-id",
        )
        url = unquote(get.call_args.args[0])
        self.assertTrue(url.endswith("/Oxbow Reentry/Courts (Navigating Court Cases)"))


if __name__ == "__main__":
    unittest.main()
