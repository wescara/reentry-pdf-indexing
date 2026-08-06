#!/usr/bin/env python3
"""
Audit and repair Reentry Documents links in Knack so they are anonymous/shareable.

What it does:
- Reads Reentry Documents records from Knack (object_14)
- Finds records in field_176 that are not anonymous links
- Resolves SharePoint item IDs from current URL path (preferred) or CSV file_id fallback
- Creates anonymous links via Microsoft Graph createLink
- Updates field_176 with the anonymous URL

Usage:
  python repair_reentry_document_links.py
  python repair_reentry_document_links.py --dry-run
"""

import argparse
import csv
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests
from dotenv import load_dotenv

load_dotenv(".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

REENTRY_DOCS_TABLE_ID = "object_14"
FIELD_FILENAME = "field_166"
FIELD_CATEGORY = "field_175"
FIELD_URL = "field_176"
SEARCH_INDEX_FULL = "search_index_full.csv"


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    raise RuntimeError(f"Missing required environment value: one of {', '.join(names)}")


def get_knack_headers() -> Dict[str, str]:
    return {
        "X-Knack-Application-Id": first_env("KNACK_APP_ID", "KNACK_APPLICATION_ID"),
        "X-Knack-REST-API-Key": first_env("KNACK_API_KEY", "KNACK_REST_API_KEY"),
        "Content-Type": "application/json",
    }


def get_knack_api_base() -> str:
    return (os.getenv("KNACK_API_URL") or "https://api.knack.com/v1").rstrip("/")


def get_graph_headers() -> Dict[str, str]:
    tenant_id = first_env("TENANT_ID")
    client_id = first_env("CLIENT_ID")
    client_secret = first_env("CLIENT_SECRET")

    token_resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise RuntimeError("Could not acquire Graph token")
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def resolve_site_id(graph_headers: Dict[str, str]) -> str:
    sharepoint_site_url = first_env("SHAREPOINT_SITE_URL")
    parsed = urlparse(sharepoint_site_url)
    site_ref = f"{parsed.hostname}:/{parsed.path.strip('/')}"
    resp = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_ref}", headers=graph_headers, timeout=30)
    resp.raise_for_status()
    site_id = resp.json().get("id")
    if not site_id:
        raise RuntimeError("Could not resolve SharePoint site id")
    return site_id


def get_knack_records() -> List[Dict]:
    base = get_knack_api_base()
    resp = requests.get(
        f"{base}/objects/{REENTRY_DOCS_TABLE_ID}/records?rows_per_page=1000&format=both",
        headers=get_knack_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("records", [])


def get_record_url(record: Dict) -> str:
    raw = record.get(f"{FIELD_URL}_raw")
    if isinstance(raw, dict):
        url = (raw.get("url") or "").strip()
        if url:
            return url
    rendered = record.get(FIELD_URL) or ""
    match = re.search(r'href="([^"]+)"', rendered)
    if match:
        return match.group(1).strip()
    return ""


def is_anonymous_url(url: str) -> bool:
    u = (url or "").lower()
    return "/:b:/s/" in u or "/:w:/s/" in u or "/:f:/s/" in u


def parse_relative_path_from_direct_url(url: str) -> Optional[str]:
    """
    Convert a direct SharePoint URL into graph root-relative path.
    Example:
      .../Shared%20Documents/Oxbow%20Reentry/Housing/file.pdf -> Oxbow Reentry/Housing/file.pdf
    """
    if not url:
        return None

    parsed = urlparse(url)
    path = unquote(parsed.path)

    marker = "/Shared Documents/"
    idx = path.find(marker)
    if idx == -1:
        return None

    rel = path[idx + len(marker):].strip("/")
    if not rel:
        return None
    return rel


def get_item_id_by_relative_path(site_id: str, relative_path: str, graph_headers: Dict[str, str]) -> Optional[str]:
    encoded_path = requests.utils.requote_uri(relative_path)
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{encoded_path}"
    resp = requests.get(url, headers=graph_headers, timeout=30)
    if resp.status_code >= 400:
        return None
    return resp.json().get("id")


def create_anonymous_link(site_id: str, item_id: str, graph_headers: Dict[str, str]) -> Optional[str]:
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/createLink",
        headers=graph_headers,
        json={"type": "view", "scope": "anonymous"},
        timeout=30,
    )
    if resp.status_code >= 400:
        return None
    return resp.json().get("link", {}).get("webUrl")


def load_csv_file_id_lookup() -> Dict[Tuple[str, str], str]:
    lookup: Dict[Tuple[str, str], str] = {}
    if not os.path.exists(SEARCH_INDEX_FULL):
        return lookup

    with open(SEARCH_INDEX_FULL, "r", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            filename = (row.get("filename") or "").strip().lower()
            category = (row.get("parent_category") or "").strip().lower()
            file_id = (row.get("file_id") or "").strip()
            if filename and category and file_id:
                lookup[(filename, category)] = file_id
    return lookup


def create_anonymous_link_from_file_id(site_id: str, file_id: str, graph_headers: Dict[str, str]) -> Optional[str]:
    return create_anonymous_link(site_id, file_id, graph_headers)


def update_knack_url(record_id: str, url: str) -> None:
    base = get_knack_api_base()
    payload = {FIELD_URL: {"url": url, "label": None}}
    resp = requests.put(
        f"{base}/objects/{REENTRY_DOCS_TABLE_ID}/records/{record_id}",
        headers=get_knack_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()


def repair_links(dry_run: bool = False) -> Dict[str, int]:
    graph_headers = get_graph_headers()
    site_id = resolve_site_id(graph_headers)
    records = get_knack_records()
    csv_lookup = load_csv_file_id_lookup()

    to_fix = []
    for record in records:
        current_url = get_record_url(record)
        if not current_url:
            continue
        if not is_anonymous_url(current_url):
            to_fix.append(record)

    logger.info("Reentry documents: %s total, %s non-anonymous link(s)", len(records), len(to_fix))

    fixed = 0
    skipped = 0
    failed = 0

    for record in to_fix:
        record_id = record.get("id", "")
        filename = (record.get(FIELD_FILENAME) or "").strip()
        category = (record.get(FIELD_CATEGORY) or "").strip()
        current_url = get_record_url(record)

        item_id: Optional[str] = None

        rel_path = parse_relative_path_from_direct_url(current_url)
        if rel_path:
            item_id = get_item_id_by_relative_path(site_id, rel_path, graph_headers)

        anon_url: Optional[str] = None
        if item_id:
            anon_url = create_anonymous_link(site_id, item_id, graph_headers)

        if not anon_url:
            file_id = csv_lookup.get((filename.lower(), category.lower()))
            if file_id:
                anon_url = create_anonymous_link_from_file_id(site_id, file_id, graph_headers)

        if not anon_url:
            logger.warning("Skip %s (%s): could not create anonymous link", record_id, filename)
            skipped += 1
            continue

        if dry_run:
            logger.info("DRY RUN would update %s (%s)", record_id, filename)
            fixed += 1
        else:
            try:
                update_knack_url(record_id, anon_url)
                logger.info("Updated %s (%s)", record_id, filename)
                fixed += 1
            except Exception as exc:
                logger.warning("Failed to update %s (%s): %s", record_id, filename, exc)
                failed += 1

        time.sleep(0.15)

    return {
        "total": len(records),
        "to_fix": len(to_fix),
        "fixed": fixed,
        "skipped": skipped,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair non-anonymous Reentry document links in Knack")
    parser.add_argument("--dry-run", action="store_true", help="Preview updates without writing to Knack")
    args = parser.parse_args()

    summary = repair_links(dry_run=args.dry_run)
    logger.info("Done: %s", summary)


if __name__ == "__main__":
    main()
