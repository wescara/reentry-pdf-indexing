#!/usr/bin/env python3
"""
Microsoft Graph SharePoint (app-only) — Oxbow Reentry PDFs: enumerate, download, and extract text.

Features:
- Loads secrets from .env (TENANT_ID, CLIENT_ID, CLIENT_SECRET)
- SharePoint site configuration (SHAREPOINT_SITE_URL, SHAREPOINT_DRIVE_NAME)
- Acquires app-only token via MSAL client credentials flow
- Resolves SharePoint site and document library by name
- Recursively enumerates all subfolders and filters PDFs
- Handles paging and retries (429/5xx)
- Saves a timestamped CSV of PDF metadata
- Downloads PDFs to local folder (DOWNLOAD_DIR), extracts text using pdfplumber
- Saves a timestamped CSV of text stats, plus one .txt file per PDF

Prereqs:
- pip install msal requests python-dotenv pdfplumber easyocr pillow numpy
- .env in project root with: TENANT_ID, CLIENT_ID, CLIENT_SECRET
- Graph Application permissions: Sites.Read.All, Files.Read.All + Admin consent
- SharePoint site configured with SHAREPOINT_SITE_URL and SHAREPOINT_DRIVE_NAME
"""

import os
import sys
import csv
import time
import logging
import glob
import shutil
import warnings
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

import requests
from msal import ConfidentialClientApplication
from dotenv import load_dotenv, find_dotenv
import pdfplumber
import easyocr
import numpy as np
from PIL import Image
import io

# Suppress pdfplumber font descriptor warnings (harmless warnings about malformed PDF font metadata)
warnings.filterwarnings('ignore', message='Could get FontBBox from font descriptor')

# ----------------------------
# Load environment (.env)
# ----------------------------
load_dotenv(find_dotenv())

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,  # change to logging.DEBUG for verbose logs
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ----------------------------
# Config from environment
# ----------------------------
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# SharePoint configuration
SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")  # e.g., https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments
SHAREPOINT_SITE_NAME = os.getenv("SHAREPOINT_SITE_NAME")  # e.g., OxbowReentryDocuments
SHAREPOINT_DRIVE_NAME = os.getenv("SHAREPOINT_DRIVE_NAME", "Shared Documents")  # Document library name

# Target folder configuration
TARGET_FOLDER_PATH = os.getenv("TARGET_FOLDER_PATH", "Oxbow Reentry")  # path within the library
TARGET_FOLDER_NAME = os.getenv("TARGET_FOLDER_NAME", "Oxbow Reentry")  # name for search fallback
SAVE_CSV = os.getenv("SAVE_CSV", "false").lower() in ("1", "true", "yes", "y")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
EXTRACT_TEXT = os.getenv("EXTRACT_TEXT", "false").lower() in ("1", "true", "yes", "y")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else None
SCOPE = ["https://graph.microsoft.com/.default"]  # Application permissions
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_TIMEOUT = 25  # seconds


def validate_config(require_sharepoint: bool = False):
    """Ensure required configuration is present before proceeding."""
    missing = []
    if not TENANT_ID:
        missing.append("TENANT_ID")
    if not CLIENT_ID:
        missing.append("CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("CLIENT_SECRET")
    if require_sharepoint:
        if not SHAREPOINT_SITE_URL:
            missing.append("SHAREPOINT_SITE_URL")
        if not SHAREPOINT_SITE_NAME:
            missing.append("SHAREPOINT_SITE_NAME")
    
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\n\nFix:\n"
            "- Ensure your .env file (in project root) contains these keys with values.\n"
            "- Or export them in your shell / configure VS Code launch.json envFile.\n"
        )


def get_access_token() -> str:
    """
    Acquire an app-only access token using MSAL client credentials flow.
    Requires Application permissions and admin consent on the Graph API.
    """
    validate_config()

    logger.debug("Initializing MSAL ConfidentialClientApplication")
    app = ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )

    logger.info("Requesting access token from Microsoft identity platform...")
    result = app.acquire_token_for_client(scopes=SCOPE)

    if "access_token" in result:
        token = result["access_token"]
        logger.info("Access token acquired.")
        logger.debug("Token (truncated): %s...", token[:50])
        return token

    # MSAL error handling
    logger.error("MSAL token acquisition failed: %s", result)
    raise RuntimeError(
        f"Token acquisition failed: {result.get('error')} - {result.get('error_description')}"
    )


def graph_request(
    method: str,
    path: str,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Generic helper to call Microsoft Graph with basic retry on 429/5xx.
    `path` should be relative to GRAPH_BASE (e.g., 'sites/contoso.sharepoint.com:/sites/SiteName').
    """
    url = f"{GRAPH_BASE}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as ex:
            logger.warning("Network error on attempt %d: %s", attempt, ex)
            if attempt == max_retries:
                raise
            time.sleep(2 * attempt)
            continue

        # Handle rate limiting
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "2"))
            logger.warning(
                "Received 429 Too Many Requests. Retrying after %s seconds (attempt %d/%d).",
                retry_after,
                attempt,
                max_retries,
            )
            time.sleep(retry_after)
            continue

        # Retry on transient server errors
        if 500 <= resp.status_code < 600 and attempt < max_retries:
            logger.warning(
                "Server error %s. Retrying (attempt %d/%d)...",
                resp.status_code,
                attempt,
                max_retries,
            )
            time.sleep(2 * attempt)
            continue

        # Non-OK responses
        if not resp.ok:
            try:
                err_json = resp.json()
            except ValueError:
                err_json = {"raw": resp.text}
            logger.error("Graph API error: %s %s -> %s", method, url, err_json)
            resp.raise_for_status()

        # Success
        try:
            return resp.json()
        except ValueError:
            # In rare cases Graph might return no JSON (e.g., 204)
            return {"status_code": resp.status_code, "text": resp.text}


# ----------------------------
# Paging helper for large collections
# ----------------------------
def graph_get_all_pages(first_page: dict, token: str) -> list:
    """
    Given the first page of a Graph response (with possible @odata.nextLink),
    fetch all subsequent pages and return a combined list of 'value' items.
    """
    items = first_page.get("value", [])
    next_link = first_page.get("@odata.nextLink")
    headers = {"Authorization": f"Bearer {token}"}

    while next_link:
        resp = requests.get(next_link, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        page = resp.json()
        items.extend(page.get("value", []))
        next_link = page.get("@odata.nextLink")

    return items


# ----------------------------
# SharePoint site and drive helpers
# ----------------------------
def get_sharepoint_site_id(site_url: str, token: str) -> str:
    """
    Resolve SharePoint site URL to site ID.
    Example: https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments
    Returns: wescaradatasolutions.sharepoint.com,10f83855-8a45-4f8e-b3fb-03fd1688722d,335c43f4-fcaf-4655-af4d-c32c0fc6ea61
    """
    # Parse the URL to extract hostname and path
    from urllib.parse import urlparse
    parsed = urlparse(site_url)
    hostname = parsed.hostname  # e.g., wescaradatasolutions.sharepoint.com
    path = parsed.path.strip("/")  # e.g., sites/OxbowReentryDocuments
    
    # First resolve using URL format to get the actual site ID
    site_id_url_format = f"{hostname}:/{path}"
    site_info = graph_request("GET", f"sites/{site_id_url_format}", token)
    
    actual_site_id = site_info.get("id")
    if actual_site_id:
        logger.info(f"Resolved SharePoint site: {actual_site_id}")
        return actual_site_id
    else:
        raise RuntimeError(f"Could not resolve site ID from URL: {site_url}")


def get_sharepoint_drive(site_id: str, drive_name: str, token: str) -> dict:
    """
    Get the drive (document library) by name from a SharePoint site.
    Example: drive_name = 'Shared Documents'
    """
    # First, get the site to verify access
    site_info = graph_request("GET", f"sites/{site_id}", token)
    logger.info(f"Connected to SharePoint site: {site_info.get('displayName')}")
    
    # List all drives in the site
    drives_response = graph_request("GET", f"sites/{site_id}/drives", token)
    drives = drives_response.get("value", [])
    
    # Find the drive by name (case-insensitive)
    drive_name_lower = drive_name.lower()
    for drive in drives:
        if drive.get("name", "").lower() == drive_name_lower:
            logger.info(f"Found drive: {drive.get('name')} (ID: {drive.get('id')})")
            return drive
    
    # If not found, raise error
    available = [d.get("name") for d in drives]
    raise RuntimeError(
        f"Drive '{drive_name}' not found in site. Available drives: {available}"
    )


def list_drive_root_items(site_id: str, drive_id: str, token: str, top: int = 200) -> list:
    first = graph_request(
        "GET", f"sites/{site_id}/drive/root/children", token, params={"$top": top}
    )
    return graph_get_all_pages(first, token)


def list_children_by_item_id(site_id: str, item_id: str, token: str, top: int = 200) -> list:
    first = graph_request(
        "GET",
        f"sites/{site_id}/drive/items/{item_id}/children",
        token,
        params={"$top": top},
    )
    return graph_get_all_pages(first, token)


def search_items(site_id: str, query: str, token: str, top: int = 50) -> list:
    first = graph_request(
        "GET",
        f"sites/{site_id}/drive/root/search(q='{query}')",
        token,
        params={"$top": top},
    )
    return graph_get_all_pages(first, token)


def search_folder_exact_ci(site_id: str, name: str, token: str, top: int = 200) -> Optional[dict]:
    """
    Searches for folders under root whose name matches `name` (case-insensitive).
    Returns the first exact-name match with type 'folder', or None.
    """
    matches = search_items(site_id, name, token, top=top)
    name_lower = name.lower()
    candidates = [m for m in matches if "folder" in m and m.get("name", "").lower() == name_lower]
    return candidates[0] if candidates else None


def list_all_files_recursive_by_item_id(site_id: str, start_item_id: str, token: str) -> list:
    """
    Recursively enumerate files (not folders) starting from a drive item id.
    """
    results = []

    def walk(item_id: str):
        first = graph_request("GET", f"sites/{site_id}/drive/items/{item_id}/children", token)
        children = graph_get_all_pages(first, token)
        for child in children:
            if "folder" in child:
                walk(child["id"])
            else:
                results.append(child)

    walk(start_item_id)
    return results


def filter_files(items: list, extensions: tuple = (".pdf",)) -> list:
    """
    Keep only items that are files and match the given extensions (case-insensitive).
    """
    kept = []
    for it in items:
        if "file" in it:
            name = it.get("name", "")
            if any(name.lower().endswith(ext.lower()) for ext in extensions):
                kept.append(it)
    return kept


# ----------------------------
# CSV: metadata index (non-timestamped, truncate on each run)
# ----------------------------
def cleanup_old_timestamped_files(base_dir: str = "."):
    """
    Delete old timestamped CSV files and .txt extracts to keep directory clean.
    """
    # Delete timestamped CSV files
    for pattern in ["pdf_index_*.csv", "pdf_text_index_*.csv"]:
        for file in glob.glob(os.path.join(base_dir, pattern)):
            try:
                os.remove(file)
                logger.info(f"Deleted old timestamped file: {file}")
            except Exception as ex:
                logger.warning(f"Failed to delete {file}: {ex}")
    
    # Delete .txt extract files from downloads folder
    downloads_dir = os.path.join(base_dir, DOWNLOAD_DIR)
    if os.path.exists(downloads_dir):
        for txt_file in glob.glob(os.path.join(downloads_dir, "**/*.txt"), recursive=True):
            try:
                os.remove(txt_file)
                logger.info(f"Deleted text extract: {txt_file}")
            except Exception as ex:
                logger.warning(f"Failed to delete {txt_file}: {ex}")


def format_file_size(size_bytes: int) -> str:
    """
    Convert bytes to KB or MB as appropriate.
    Returns formatted string like '512.5 KB' or '2.3 MB'
    """
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    
    if size_bytes < 1024 * 1024:  # Less than 1 MB, show as KB
        kb = size_bytes / 1024
        return f"{kb:.1f} KB" if kb >= 10 else f"{kb:.2f} KB"
    else:
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.2f} MB"


def get_file_size_kb(size_bytes: int) -> float:
    """Convert bytes to KB as decimal (for numeric storage in Knack)."""
    return round((size_bytes or 0) / 1024, 2)


def save_pdfs_meta_to_csv(items: list, csv_path: str = "pdfs_metadata.csv"):
    """
    Save a CSV with the PDF metadata found.
    Columns: name, size_bytes, size_mb, lastModifiedDateTime, webUrl, id, parentPath, parentId
    Overwrites existing file (non-timestamped).
    """
    fields = ["name", "size_bytes", "size_mb", "lastModifiedDateTime", "webUrl", "id", "parentPath", "parentId"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for it in items:
            parent_path = it.get("parentReference", {}).get("path")
            parent_id = it.get("parentReference", {}).get("id")
            size_bytes = it.get("size") or 0
            size_kb = get_file_size_kb(size_bytes)
            w.writerow({
                "name": it.get("name"),
                "size_bytes": size_bytes,
                "size_mb": size_kb,
                "lastModifiedDateTime": it.get("lastModifiedDateTime"),
                "webUrl": it.get("webUrl"),
                "id": it.get("id"),
                "parentPath": parent_path,
                "parentId": parent_id,
            })
    print(f"Saved metadata CSV: {csv_path}")


# ----------------------------
# Download & text extraction
# ----------------------------
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """
    Clean filename by fixing mojibake characters and normalizing special characters.
    
    Handles:
    - UTF-8 mojibake patterns (e.g., â€" → —, â€œ → ", etc.)
    - Removes control characters and normalizes whitespace
    - Preserves legitimate Unicode characters (em-dashes, quotes, etc.)
    """
    # Mojibake fixes: Latin-1 misinterpretations of UTF-8 bytes
    mojibake_map = {
        'â€"': '–',      # en-dash (U+2013)
        'â€"': '—',      # em-dash (U+2014)
        'â€œ': '"',      # left double quote
        'â€\x9d': '"',   # right double quote
        'â€™': "'",      # right single quote
        'â€˜': "'",      # left single quote
        'â€¢': '•',      # bullet
        'ï¬€': 'ff',     # ff ligature
        'ï¬‚': 'fi',     # fi ligature
        'ï¬ƒ': 'fl',     # fl ligature
        'ï¬…': 'ffi',    # ffi ligature
        'ï¬†': 'ffl',    # ffl ligature
        'â€¦': '…',      # ellipsis
        'â€"': '–',      # another en-dash variant
    }
    
    result = filename
    for mojibake, correct in mojibake_map.items():
        result = result.replace(mojibake, correct)
    
    # Remove control characters (0x00-0x1F except tab/newline)
    result = ''.join(c for c in result if ord(c) >= 32 or c in '\t\n')
    
    # Normalize whitespace (multiple spaces/tabs → single space)
    result = ' '.join(result.split())
    
    return result


def local_path_for_item(download_dir: str, item: dict) -> str:
    """
    Build a local file path based on parentReference.path and item name.
    Example parent path: "/drive/root:/Oxbow Reentry/Health"
    Sanitizes filename to fix mojibake and special characters.
    """
    parent_path = item.get("parentReference", {}).get("path", "")
    # Normalize to relative path rooted at download_dir:
    # strip "/drive/root:" prefix if present
    rel = parent_path.replace("/drive/root:", "").lstrip("/")
    safe_rel = rel.replace(":", "")  # colon not allowed on Windows (for portability)
    dest_dir = os.path.join(download_dir, safe_rel)
    ensure_dir(dest_dir)
    # Sanitize filename to fix mojibake and special characters
    clean_filename = sanitize_filename(item.get("name", "file.pdf"))
    return os.path.join(dest_dir, clean_filename)


def download_pdf_by_item_id(site_id: str, item_id: str, token: str, dest_path: str, timeout: int = DEFAULT_TIMEOUT):
    """
    Download file content via Graph: /sites/{siteId}/drive/items/{id}/content
    """
    url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"
    headers = {"Authorization": f"Bearer {token}"}
    with requests.get(url, headers=headers, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)


def extract_text_from_pdf(file_path: str) -> Tuple[int, int, str, str]:
    """
    Extract text from PDF using pdfplumber. If extraction yields <100 chars per page average,
    use OCR (easyocr) as fallback for scanned documents.
    Returns (page_count, char_count, text, extraction_method).
    extraction_method: "pdfplumber", "easyocr", or "failed"
    """
    try:
        with pdfplumber.open(file_path) as pdf:
            pages = len(pdf.pages)
            chunks = []
            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                    chunks.append(text)
                except Exception:
                    chunks.append("")
            full_text = "\n".join(chunks)
            
            # Check if we got sufficient text (average >100 chars per page)
            avg_chars_per_page = len(full_text) / max(pages, 1)
            if avg_chars_per_page >= 100:
                return pages, len(full_text), full_text, "pdfplumber"
            
            # Not enough text - try OCR
            logger.info(f"Low text extraction ({len(full_text)} chars in {pages} pages). Attempting OCR...")
            try:
                # Initialize OCR reader (English only for speed, adjust if needed)
                reader = easyocr.Reader(['en'], gpu=False, verbose=False)
                ocr_chunks = []
                
                for i, page in enumerate(pdf.pages):
                    try:
                        # Convert PDF page to PIL Image
                        pil_image = page.to_image(resolution=150).original
                        # Convert PIL Image to numpy array
                        img_array = np.array(pil_image)
                        # Run OCR on the image
                        results = reader.readtext(img_array)
                        # Extract text from OCR results
                        page_text = "\n".join([text for (_, text, _) in results])
                        ocr_chunks.append(page_text)
                    except Exception as e:
                        logger.warning(f"OCR failed on page {i+1}: {e}")
                        ocr_chunks.append("")
                
                ocr_text = "\n".join(ocr_chunks)
                if len(ocr_text) > len(full_text):
                    logger.info(f"OCR successful: {len(ocr_text)} chars extracted")
                    return pages, len(ocr_text), ocr_text, "easyocr"
                else:
                    # OCR didn't help, return original
                    return pages, len(full_text), full_text, "pdfplumber"
            except Exception as ocr_ex:
                logger.warning(f"OCR attempt failed: {ocr_ex}. Using original extraction.")
                return pages, len(full_text), full_text, "pdfplumber"
                
    except Exception as ex:
        logger.warning("Failed to extract text from '%s': %s", file_path, ex)
        return 0, 0, "", "failed"


def save_pdfs_text_to_csv(rows: list, csv_path: str = "pdf_text_stats.csv"):
    """
    Save text stats CSV. Columns: name, id, local_path, parentPath, page_count, text_chars, extraction_method, needs_ocr
    Overwrites existing file (non-timestamped).
    """
    fields = ["name", "id", "local_path", "parentPath", "page_count", "text_chars", "extraction_method", "needs_ocr"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Saved text CSV: {csv_path}")


# ----------------------------
# Main
# ----------------------------
def main():
    start_time = time.time()
    try:
        # Clean up old timestamped files before starting
        logger.info("Cleaning up old timestamped CSV files and text extracts...")
        cleanup_old_timestamped_files()

        validate_config(require_sharepoint=True)
        token = get_access_token()

        # --- Resolve SharePoint site and drive ---
        site_id = get_sharepoint_site_id(SHAREPOINT_SITE_URL, token)
        drive = get_sharepoint_drive(site_id, SHAREPOINT_DRIVE_NAME, token)
        drive_id = drive.get("id")
        logger.info(f"Using drive: {drive.get('name')} (ID: {drive_id})")

        # --- Resolve target folder: prefer exact PATH, fallback to NAME search (case-insensitive) ---
        folder_item = None

        # Try resolving by path (preferred)
        try:
            start_item = graph_request(
                "GET", 
                f"sites/{site_id}/drive/root:/{TARGET_FOLDER_PATH}", 
                token
            )
            if "id" in start_item and "folder" in start_item:
                folder_item = start_item
                print(f"\nFolder by path resolved: '{TARGET_FOLDER_PATH}' | Item ID: {folder_item['id']}")
            else:
                raise RuntimeError("Path did not resolve to a folder.")
        except Exception as ex:
            logger.warning("Path resolve failed for '%s': %s", TARGET_FOLDER_PATH, ex)
            print(f"\nResolving by name search (case-insensitive): '{TARGET_FOLDER_NAME}' ...")
            candidate = search_folder_exact_ci(site_id, TARGET_FOLDER_NAME, token)
            if not candidate:
                raise RuntimeError(f"No folder named '{TARGET_FOLDER_NAME}' found via search.")
            folder_item = candidate
            parent_path = folder_item.get("parentReference", {}).get("path")
            print(f"Folder found: '{folder_item.get('name')}' | Item ID: {folder_item['id']} | Parent: {parent_path or '(root?)'}")

        # --- List direct children of the folder (sanity check) ---
        children = list_children_by_item_id(site_id, folder_item["id"], token)
        print(f"\n=== Items in '{TARGET_FOLDER_PATH or TARGET_FOLDER_NAME}' (count: {len(children)}) ===")
        for it in children[:50]:
            kind = "folder" if "folder" in it else "file"
            print(f"- {it.get('name')} [{kind}]")

        # --- Recursively enumerate all files under the folder, filter PDFs ---
        all_files = list_all_files_recursive_by_item_id(site_id, folder_item["id"], token)
        pdfs = filter_files(all_files, extensions=(".pdf",))
        print(f"\n=== PDFs under '{TARGET_FOLDER_PATH or TARGET_FOLDER_NAME}' (count: {len(pdfs)}) ===")
        for f in pdfs[:50]:  # preview top 50
            size_formatted = format_file_size(f.get('size') or 0)
            print(f"- {f.get('name')} | {size_formatted} | modified: {f.get('lastModifiedDateTime')} | webUrl: {f.get('webUrl')}")

        # --- Save metadata CSV (optional) ---
        if SAVE_CSV:
            save_pdfs_meta_to_csv(pdfs)

        # --- Download & extract text (optional) ---
        if EXTRACT_TEXT:
            ensure_dir(DOWNLOAD_DIR)
            text_rows = []
            for idx, item in enumerate(pdfs, start=1):
                local_path = local_path_for_item(DOWNLOAD_DIR, item)
                print(f"({idx}/{len(pdfs)}) Downloading -> {local_path}")
                try:
                    download_pdf_by_item_id(site_id, item["id"], token, local_path)
                except Exception as ex:
                    logger.error("Download failed for '%s': %s", item.get("name"), ex)
                    continue

                pages, chars, text, extraction_method = extract_text_from_pdf(local_path)
                needs_ocr = (chars < 50)  # heuristic: very little text => likely scanned image
                if extraction_method == "easyocr":
                    print(f"  [OCR] Extracted {chars} chars via EasyOCR")
                text_rows.append({
                    "name": item.get("name"),
                    "id": item.get("id"),
                    "local_path": local_path,
                    "parentPath": item.get("parentReference", {}).get("path"),
                    "page_count": pages,
                    "text_chars": chars,
                    "extraction_method": extraction_method,
                    "needs_ocr": needs_ocr,
                })

                # Save per-file text alongside PDF (optional)
                txt_path = local_path + ".txt"
                try:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception as ex:
                    logger.warning("Failed to save text file '%s': %s", txt_path, ex)

            # Save text CSV
            save_pdfs_text_to_csv(text_rows)

    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)
    finally:
        elapsed = time.time() - start_time
        minutes, seconds = divmod(elapsed, 60)
        print(f"\n{'='*80}")
        print(f"Processing completed in {int(minutes)}m {int(seconds)}s")
        print(f"{'='*80}")


if __name__ == "__main__":
    main()
