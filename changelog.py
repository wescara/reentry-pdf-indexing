#!/usr/bin/env python3
"""
Change log for pipeline runs.
Compares search_index_full.csv before/after each run to detect additions,
removals, and modifications. Persists results to pipeline_changelog.json.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

CHANGELOG_FILE = "pipeline_changelog.json"
SEARCH_INDEX_FULL = "search_index_full.csv"
METADATA_CSV = "pdfs_metadata.csv"


def _load_index(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def snapshot_before() -> Optional[pd.DataFrame]:
    """Take a snapshot of the current index before a pipeline run."""
    return _load_index(SEARCH_INDEX_FULL)


def compute_changes(before_df: pd.DataFrame, after_df: pd.DataFrame) -> Dict:
    """Compare before/after DataFrames and return a change summary."""
    if before_df.empty and after_df.empty:
        return {"added": [], "removed": [], "modified": [], "unchanged_count": 0}

    if before_df.empty:
        return {
            "added": after_df["filename"].tolist(),
            "removed": [],
            "modified": [],
            "unchanged_count": 0,
        }

    if after_df.empty:
        return {
            "added": [],
            "removed": before_df["filename"].tolist(),
            "modified": [],
            "unchanged_count": 0,
        }

    def _key(row):
        return (str(row.get("filename", "")), str(row.get("parent_category", "")))

    def _label(k):
        fname, cat = k
        return f"{fname} ({cat})" if cat else fname

    before_keys = set(before_df.apply(_key, axis=1))
    after_keys = set(after_df.apply(_key, axis=1))

    added = sorted([_label(k) for k in after_keys - before_keys])
    removed = sorted([_label(k) for k in before_keys - after_keys])

    common = before_keys & after_keys
    modified = []

    if common:
        before_indexed = before_df.set_index(["filename", "parent_category"]).sort_index()
        after_indexed = after_df.set_index(["filename", "parent_category"]).sort_index()

        compare_cols = [c for c in ["last_modified", "page_count", "text_chars", "file_size_mb"]
                        if c in before_indexed.columns and c in after_indexed.columns]

        for key in sorted(common):
            try:
                for col in compare_cols:
                    bval = str(before_indexed.loc[key].get(col, ""))
                    aval = str(after_indexed.loc[key].get(col, ""))
                    if bval != aval:
                        modified.append(_label(key))
                        break
            except KeyError:
                pass

    unchanged_count = len(common) - len(modified)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": unchanged_count,
    }


def find_duplicates() -> List[Dict]:
    """Find files that appear in multiple SharePoint subfolders."""
    if not os.path.exists(METADATA_CSV):
        return []
    meta = pd.read_csv(METADATA_CSV)
    dupes = meta.groupby("name").filter(lambda g: len(g) > 1)
    if dupes.empty:
        return []
    results = []
    for name, group in dupes.groupby("name"):
        folders = [p.replace("/drive/root:/", "") for p in group["parentPath"].dropna()]
        for folder in folders:
            results.append({"filename": name, "folder": folder})
    return sorted(results, key=lambda r: r["filename"])


def record_run(before_df: pd.DataFrame) -> Dict:
    """Compare the pre-run snapshot to the current index and save a log entry."""
    after_df = _load_index(SEARCH_INDEX_FULL)
    changes = compute_changes(before_df, after_df)

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total_documents": len(after_df),
        "added": changes["added"],
        "removed": changes["removed"],
        "modified": changes["modified"],
        "unchanged_count": changes["unchanged_count"],
        "duplicates": find_duplicates(),
    }

    log = load_changelog()
    log.insert(0, entry)
    # Keep last 50 runs
    log = log[:50]

    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    return entry


def load_changelog() -> List[Dict]:
    """Load the changelog from disk."""
    if not os.path.exists(CHANGELOG_FILE):
        return []
    with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
