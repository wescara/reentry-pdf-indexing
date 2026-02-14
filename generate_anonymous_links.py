#!/usr/bin/env python3
"""
Generate anonymous sharing links for all PDFs in SharePoint.
Creates "anonymous link" (anyone with link can view) for each file.
Updates search_index_full.csv with the new shareable links.

Usage:
    python generate_anonymous_links.py
"""

import os
import sys
import csv
import time
import logging
from typing import Dict, Optional
from datetime import datetime

import requests
from dotenv import load_dotenv, find_dotenv

# Load environment
load_dotenv(find_dotenv())

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Config
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")
SEARCH_INDEX_FULL = "search_index_full.csv"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_TIMEOUT = 25


def validate_config():
    """Ensure required configuration is present."""
    missing = []
    if not TENANT_ID:
        missing.append("TENANT_ID")
    if not CLIENT_ID:
        missing.append("CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("CLIENT_SECRET")
    if not SHAREPOINT_SITE_URL:
        missing.append("SHAREPOINT_SITE_URL")
    
    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing) + "\n"
            "Ensure your .env file has all required values."
        )


def get_access_token() -> str:
    """Acquire an app-only access token."""
    from msal import ConfidentialClientApplication
    
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    app = ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority,
        client_credential=CLIENT_SECRET,
    )
    
    logger.info("Acquiring access token...")
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    
    if "access_token" in result:
        logger.info("Access token acquired.")
        return result["access_token"]
    
    logger.error("Token acquisition failed: %s", result)
    raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")


def graph_request(
    method: str,
    path: str,
    token: str,
    params: Optional[Dict] = None,
    json: Optional[Dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """Make a Graph API request."""
    url = f"{GRAPH_BASE}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        resp = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
        )
        
        if not resp.ok:
            logger.error("Graph error: %s %s -> %s", method, url, resp.text)
            resp.raise_for_status()
        
        return resp.json() if resp.text else {}
    except requests.RequestException as e:
        logger.error(f"Request error: {e}")
        raise


def get_sharepoint_site_id(token: str) -> str:
    """Resolve SharePoint site URL to site ID."""
    from urllib.parse import urlparse
    
    parsed = urlparse(SHAREPOINT_SITE_URL)
    hostname = parsed.hostname
    path = parsed.path.strip("/")
    site_id_url_format = f"{hostname}:/{path}"
    
    site_info = graph_request("GET", f"sites/{site_id_url_format}", token)
    actual_site_id = site_info.get("id")
    
    if actual_site_id:
        logger.info(f"Resolved site ID: {actual_site_id}")
        return actual_site_id
    else:
        raise RuntimeError(f"Could not resolve site ID")


def create_anonymous_link(site_id: str, file_id: str, token: str) -> Optional[str]:
    """
    Create an anonymous sharing link for a file.
    Returns the link URL, or None if creation fails.
    """
    try:
        # Create a sharing link with role=view and scope=anonymous
        endpoint = f"sites/{site_id}/drive/items/{file_id}/createLink"
        
        payload = {
            "type": "view",  # read-only access
            "scope": "anonymous"  # anyone with link can access
        }
        
        response = graph_request("POST", endpoint, token, json=payload)
        
        if "link" in response:
            link_url = response["link"].get("webUrl")
            if link_url:
                logger.debug(f"Created anonymous link: {link_url}")
                return link_url
        
        logger.warning(f"No link in response for file {file_id}: {response}")
        return None
        
    except Exception as e:
        logger.warning(f"Failed to create anonymous link for file {file_id}: {e}")
        return None


def update_search_index_with_anonymous_links(site_id: str, token: str):
    """
    Read search_index_full.csv, generate anonymous links using file IDs, and save updated CSV.
    Uses file IDs directly instead of searching by filename (avoids special character issues).
    """
    if not os.path.exists(SEARCH_INDEX_FULL):
        raise FileNotFoundError(f"{SEARCH_INDEX_FULL} not found")
    
    logger.info(f"Reading {SEARCH_INDEX_FULL}...")
    
    # Read CSV
    rows = []
    with open(SEARCH_INDEX_FULL, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    logger.info(f"Processing {len(rows)} PDFs...")
    
    # Process each row
    updated_count = 0
    skipped_count = 0
    
    for idx, row in enumerate(rows, start=1):
        filename = row.get("filename", "unknown")
        file_id = row.get("file_id", "")  # Fix: use "file_id" column, not "id"
        original_url = row.get("web_url", "")
        
        print(f"({idx}/{len(rows)}) {filename}")
        
        # Skip if already has an anonymous link (contains /_layouts/ or webUrl format)
        if "sharepoint.com/:b:/s/" in original_url or "sharepoint.com/:w:/s/" in original_url:
            # Already an anonymous link (short URL format)
            skipped_count += 1
            logger.debug(f"  Already has anonymous link, skipping")
            continue
        
        if not file_id:
            logger.warning(f"  No file ID available, skipping")
            skipped_count += 1
            continue
        
        try:
            # Create anonymous link using the file ID directly
            # This avoids searching by filename which fails with special characters
            anonymous_link = create_anonymous_link(site_id, file_id, token)
            
            if anonymous_link:
                row["web_url"] = anonymous_link
                updated_count += 1
                logger.info(f"  ✓ Created anonymous link")
            else:
                logger.warning(f"  Failed to create anonymous link")
        
        except Exception as e:
            logger.warning(f"  Error processing: {e}")
        
        # Rate limiting
        time.sleep(0.5)
    
    # Save updated CSV
    logger.info(f"\nSaving updated {SEARCH_INDEX_FULL}...")
    with open(SEARCH_INDEX_FULL, "w", newline="", encoding="utf-8") as f:
        if rows:
            fieldnames = rows[0].keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    
    logger.info(f"✓ Updated {updated_count}/{len(rows)} URLs with anonymous links")
    logger.info(f"✓ Skipped {skipped_count} (already have anonymous links or no ID)")
    print(f"\n✓ Completed! Updated {updated_count} files with anonymous sharing links")
    print(f"  All links now work for users without SharePoint accounts")


def main():
    try:
        validate_config()
        token = get_access_token()
        site_id = get_sharepoint_site_id(token)
        update_search_index_with_anonymous_links(site_id, token)
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
