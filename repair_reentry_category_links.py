#!/usr/bin/env python3
"""
Audit and repair missing Reentry top-section category hyperlinks in Knack.

What it does:
- Reads Reentry category records from Knack (object_13)
- Detects categories whose URL field is missing or malformed
- Resolves each category folder in SharePoint under "Oxbow Reentry/<Category>"
- Creates/gets an anonymous view link via Microsoft Graph
- Updates Knack URL field (field_163) with a proper link payload

Usage:
  python repair_reentry_category_links.py
  python repair_reentry_category_links.py --dry-run
"""

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
from dotenv import load_dotenv

load_dotenv(".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class Config:
    tenant_id: str
    client_id: str
    client_secret: str
    sharepoint_site_url: str
    knack_api_key: str
    knack_app_id: str
    knack_api_base: str
    reentry_table_id: str = "object_13"
    category_name_field: str = "field_162"
    category_url_field: str = "field_163"


def read_config() -> Config:
    tenant_id = os.getenv("TENANT_ID", "").strip()
    client_id = os.getenv("CLIENT_ID", "").strip()
    client_secret = os.getenv("CLIENT_SECRET", "").strip()
    sharepoint_site_url = os.getenv("SHAREPOINT_SITE_URL", "").strip()
    knack_api_key = (os.getenv("KNACK_API_KEY") or os.getenv("KNACK_REST_API_KEY") or "").strip()
    knack_app_id = (os.getenv("KNACK_APP_ID") or os.getenv("KNACK_APPLICATION_ID") or "").strip()
    knack_api_base = (os.getenv("KNACK_API_URL") or "https://api.knack.com/v1").strip().rstrip("/")

    missing = []
    if not tenant_id:
        missing.append("TENANT_ID")
    if not client_id:
        missing.append("CLIENT_ID")
    if not client_secret:
        missing.append("CLIENT_SECRET")
    if not sharepoint_site_url:
        missing.append("SHAREPOINT_SITE_URL")
    if not knack_api_key:
        missing.append("KNACK_API_KEY (or KNACK_REST_API_KEY)")
    if not knack_app_id:
        missing.append("KNACK_APP_ID (or KNACK_APPLICATION_ID)")

    if missing:
        raise RuntimeError("Missing required environment values: " + ", ".join(missing))

    return Config(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        sharepoint_site_url=sharepoint_site_url,
        knack_api_key=knack_api_key,
        knack_app_id=knack_app_id,
        knack_api_base=knack_api_base,
    )


def get_graph_headers(cfg: Config) -> Dict[str, str]:
    token_resp = requests.post(
        f"https://login.microsoftonline.com/{cfg.tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise RuntimeError("Failed to obtain Graph access token")
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def resolve_site_id(cfg: Config, graph_headers: Dict[str, str]) -> str:
    parsed = urlparse(cfg.sharepoint_site_url)
    site_ref = f"{parsed.hostname}:/{parsed.path.strip('/')}"
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_ref}",
        headers=graph_headers,
        timeout=30,
    )
    resp.raise_for_status()
    site_id = resp.json().get("id")
    if not site_id:
        raise RuntimeError("Failed to resolve SharePoint site ID")
    return site_id


def knack_headers(cfg: Config) -> Dict[str, str]:
    return {
        "X-Knack-Application-Id": cfg.knack_app_id,
        "X-Knack-REST-API-Key": cfg.knack_api_key,
        "Content-Type": "application/json",
    }


def get_reentry_records(cfg: Config) -> List[Dict]:
    url = f"{cfg.knack_api_base}/objects/{cfg.reentry_table_id}/records?rows_per_page=100&format=both"
    resp = requests.get(url, headers=knack_headers(cfg), timeout=30)
    resp.raise_for_status()
    return resp.json().get("records", [])


def is_missing_link(record: Dict, url_field: str) -> bool:
    raw_key = f"{url_field}_raw"
    raw_val = record.get(raw_key)
    if isinstance(raw_val, dict) and str(raw_val.get("url", "")).startswith("http"):
        return False
    return True


def get_folder_item_id(site_id: str, category_name: str, graph_headers: Dict[str, str]) -> Optional[str]:
    folder_rel = f"Oxbow Reentry/{category_name}"
    folder_path = quote(folder_rel, safe="/")
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{folder_path}",
        headers=graph_headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        return None
    return resp.json().get("id")


def create_anonymous_folder_link(site_id: str, item_id: str, graph_headers: Dict[str, str]) -> Optional[str]:
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/createLink",
        headers=graph_headers,
        json={"type": "view", "scope": "anonymous"},
        timeout=30,
    )
    if resp.status_code >= 400:
        return None
    return resp.json().get("link", {}).get("webUrl")


def update_knack_link(cfg: Config, record_id: str, url_field: str, link_url: str) -> None:
    payload = {url_field: {"url": link_url, "label": None}}
    resp = requests.put(
        f"{cfg.knack_api_base}/objects/{cfg.reentry_table_id}/records/{record_id}",
        headers=knack_headers(cfg),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()


def repair_links(dry_run: bool = False) -> int:
    cfg = read_config()
    g_headers = get_graph_headers(cfg)
    site_id = resolve_site_id(cfg, g_headers)
    records = get_reentry_records(cfg)

    missing = [r for r in records if is_missing_link(r, cfg.category_url_field)]
    logger.info("Reentry categories: %s total, %s missing link(s)", len(records), len(missing))

    repaired = 0
    skipped = 0
    for record in missing:
        record_id = record.get("id", "")
        category_name = str(record.get(cfg.category_name_field, "")).strip()
        if not category_name:
            logger.warning("Skipping %s: blank category name", record_id)
            skipped += 1
            continue

        item_id = get_folder_item_id(site_id, category_name, g_headers)
        if not item_id:
            logger.warning("Skipping %s (%s): folder not found in SharePoint", record_id, category_name)
            skipped += 1
            continue

        link_url = create_anonymous_folder_link(site_id, item_id, g_headers)
        if not link_url:
            logger.warning("Skipping %s (%s): could not create anonymous link", record_id, category_name)
            skipped += 1
            continue

        if dry_run:
            logger.info("DRY RUN would update %s (%s) -> %s", record_id, category_name, link_url)
            repaired += 1
            continue

        update_knack_link(cfg, record_id, cfg.category_url_field, link_url)
        logger.info("Updated %s (%s)", record_id, category_name)
        repaired += 1

    logger.info("Done. repaired=%s skipped=%s dry_run=%s", repaired, skipped, dry_run)
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair missing Reentry top-section category links in Knack.")
    parser.add_argument("--dry-run", action="store_true", help="Show intended updates without writing to Knack")
    args = parser.parse_args()

    try:
        repair_links(dry_run=args.dry_run)
    except Exception as exc:
        logger.error("Failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
