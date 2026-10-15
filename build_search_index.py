#!/usr/bin/env python3
"""
Build searchable index from extracted PDF text and metadata.

Reads:
- pdfs_metadata.csv: PDF file metadata
- pdf_text_stats.csv: Text extraction stats and local paths

Creates a dataframe mapping:
- Each PDF to its parent category (from folder structure)
- Extracted text content
- File metadata

Exports as `search_index.csv` for review before pushing to Knack.
"""

import os
import csv
import pandas as pd
import logging
import re
from pathlib import Path
from typing import Dict, Optional, Tuple


# ----------------------------
# Filename Sanitization
# ----------------------------
def sanitize_filename(filename: str) -> str:
    """
    Clean filename by fixing mojibake characters and normalizing special characters.
    
    Handles:
    - UTF-8 mojibake patterns (e.g., â€– → —, â€œ → ", etc.)
    - Removes control characters and normalizes whitespace
    - Preserves legitimate Unicode characters (em-dashes, quotes, etc.)
    """
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


# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ----------------------------
# SharePoint URL Generation
# ----------------------------
def convert_to_sharepoint_file_url(parent_path: str, filename: str, parent_id: str = None) -> str:
    """
    Convert OneDrive Graph API parent path + filename to SharePoint file URL using the _layouts format.
    
    Uses the same permission model as folder navigation to avoid subfolder permission issues.
    Includes viewid (folder ID), parent folder parameter for proper context.
    
    Input: 
      parent_path: /drive/root:/Oxbow Reentry/Addiction Recovery/Mentor Works
      filename: Golden Ticket Example.pdf
      parent_id: 012UR7O4ML6ZHRFOPCRZH2DEWQJNACVUSY
    Output: 
      https://wescaradatasolutions-my.sharepoint.com/personal/kelly_wescara_com/_layouts/15/onedrive.aspx?viewid=012UR...&ga=1&LOF=1&id=...&parent=...
    """
    if not parent_path or not filename:
        return ""
    
    try:
        from urllib.parse import quote
        
        # Extract the folder path from /drive/root:/path format
        if "/drive/root:" in parent_path:
            folder_path = parent_path.split("/drive/root:", 1)[1]
        else:
            folder_path = parent_path
        
        # Build the full file path: /personal/user/Documents/folder/path/filename
        full_path = f"/personal/kelly_wescara_com/Documents{folder_path}/{filename}"
        
        # Build parent path: /personal/user/Documents/folder/path (without filename)
        parent_full_path = f"/personal/kelly_wescara_com/Documents{folder_path}"
        
        # URL encode both paths - encode EVERYTHING including slashes, underscores, periods
        # This matches the format SharePoint uses when accessed through proper context
        encoded_id = quote(full_path, safe='')
        encoded_parent = quote(parent_full_path, safe='')
        
        # Build URL with ga=1&LOF=1&id&parent parameters (proven working format)
        # Note: NOT using viewid - it causes AccessDenied on some files
        sharepoint_url = f"https://wescaradatasolutions-my.sharepoint.com/personal/kelly_wescara_com/_layouts/15/onedrive.aspx?ga=1&LOF=1&id={encoded_id}&parent={encoded_parent}"
        
        return sharepoint_url
    except Exception as e:
        logger.warning(f"Failed to convert path {parent_path}/{filename}: {e}")
        return ""

# ----------------------------
# Config
# ----------------------------
METADATA_CSV = "pdfs_metadata.csv"
TEXT_STATS_CSV = "pdf_text_stats.csv"
OUTPUT_CSV = "search_index.csv"

# Categories from Knack (matches the 27 top-level folders)
KNACK_CATEGORIES = {
    "Addiction Recovery",
    "Bicycles",
    "Cell Phones and Computers",
    "Church Incarceration Resources",
    "Community Resource Lists",
    "Courts (How to Navigate Court Cases)",
    "Education/Training",
    "Employment",
    "Federal and ICE",
    "General Resources",
    "Health",
    "Homelessness",
    "Housing",
    "Other County Jails",
    "Oxbow Jail Info",
    "Sex Offender Resources",
    "TAM - Medicaid",
    "TSO and Church Support for Homeless Shelters",
    "Utah State Prison",
    "Veteran Resources",
    "Women’s Resources",
    "Women's Resources",
}


def normalize_category_name(name: str) -> str:
    """Normalize category names to handle apostrophe/whitespace variants."""
    if not isinstance(name, str):
        return ""
    normalized = " ".join(name.strip().split())
    # Normalize curly apostrophe to straight for matching.
    normalized = normalized.replace("’", "'")
    return normalized


CANONICAL_CATEGORY_BY_NORMALIZED = {
    normalize_category_name(category): category
    for category in KNACK_CATEGORIES
}
# Prefer typographic apostrophe for output consistency.
CANONICAL_CATEGORY_BY_NORMALIZED["Women's Resources"] = "Women’s Resources"
CANONICAL_CATEGORY_BY_NORMALIZED["Courts (Navigating Court Cases)"] = "Courts (How to Navigate Court Cases)"


def clean_extracted_text(text: str) -> str:
    """
    Clean extracted text for better searchability.
    
    Improvements:
    - Fix UTF-8 encoding issues (mojibake) - aggressive approach
    - Remove spaces incorrectly inserted within words
    - Normalize whitespace (multiple spaces to single)
    - Fix smart quotes and em-dashes
    - Remove control characters
    - Trim edges
    - Preserve phone numbers, addresses, emails
    """
    if not text or not isinstance(text, str):
        return ""
    
    # Fix UTF-8 encoding mojibake (common from PDF extraction)
    # Using multiple variations to catch all cases
    mojibake_fixes = {
        # Quotes and dashes
        'â€œ': '"',      # UTF-8 left double quote
        'â€\x9d': '"',   # UTF-8 right double quote variant
        'â€': '"',       # Generic variant
        'â€"': '--',     # UTF-8 em-dash (double hyphen)
        'â€"': '-',      # UTF-8 em-dash (single)
        'â€™': "'",      # UTF-8 right single quote
        'â€˜': "'",      # UTF-8 left single quote
        
        # Bullets and symbols
        'â€¢': '•',      # UTF-8 bullet point
        'âž¢': '•',      # Another bullet variant
        'â€º': '•',      # Yet another bullet variant
        'â€£': '•',      # Another variation
        
        # Ligatures and special chars
        'ï¬€': 'ff',     # Ligature ff
        'ï¬‚': 'fi',     # Ligature fi
        'ï¬ƒ': 'fl',     # Ligature fl
        'ï¬„': 'ffi',    # Ligature ffi
        'ï¬…': 'ffl',    # Ligature ffl
        'ï¬ ': 'f',      # Partial ligature
        'ï¬¡': 'f',      # Partial ligature
        
        # Other artifacts
        'Â': '',         # Non-breaking space artifacts
        'â€': '',        # UTF-8 fragment
    }
    
    for mojibake, replacement in mojibake_fixes.items():
        text = text.replace(mojibake, replacement)
    
    # Additional: try to fix any remaining UTF-8 mojibake by pattern
    # Match â€X patterns and try to replace with common punctuation
    import re
    text = re.sub(r'â€[^\s]', '', text)  # Remove stray UTF-8 fragments
    
    # Fix common encoding issues
    text = text.replace(''', "'").replace(''', "'")  # Smart quotes to regular
    text = text.replace('"', '"').replace('"', '"')   # Smart double quotes
    text = text.replace('–', '-').replace('—', '-')   # Em-dash and en-dash to hyphen
    text = text.replace('\u200b', '')                 # Zero-width space
    text = text.replace('\xa0', ' ')                  # Non-breaking space to regular
    
    # Remove control characters but keep newlines temporarily
    text = ''.join(ch for ch in text if not (ord(ch) < 32 and ch not in '\n\r\t'))
    
    # Replace multiple newlines with single space
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\r+', ' ', text)
    
    # Normalize whitespace (multiple spaces to single)
    text = re.sub(r'\s+', ' ', text)
    
    # Fix spaces incorrectly inserted within words AFTER normalizing spaces
    # Only fix patterns that clearly shouldn't have spaces
    text = re.sub(r'\b([A-Z])\s+([a-z])', r'\1\2', text)  # Capital+space+lowercase
    
    # Trim edges
    text = text.strip()
    
    return text


def extract_parent_category(parent_path: str) -> Optional[str]:
    """
    Extract the immediate child folder (category) from a OneDrive parent path.
    
    Example:
    "/drive/root:/Oxbow Reentry/Health" -> "Health"
    "/drive/root:/Oxbow Reentry/Addiction Recovery/CATS" -> "Addiction Recovery"
    """
    if not parent_path:
        return None
    
    # Remove "/drive/root:" prefix
    rel_path = parent_path.replace("/drive/root:", "").lstrip("/")
    parts = rel_path.split("/")
    
    # Skip "Oxbow Reentry" and get the next part
    if len(parts) > 1 and parts[0] == "Oxbow Reentry":
        category = parts[1]
        return CANONICAL_CATEGORY_BY_NORMALIZED.get(normalize_category_name(category), category)
    
    return None


def read_extracted_text(local_path: str) -> str:
    """
    Read extracted text from a .txt file alongside the PDF.
    If file doesn't exist, return empty string.
    """
    txt_path = local_path + ".txt"
    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as ex:
            logger.warning(f"Failed to read text from {txt_path}: {ex}")
            return ""
    return ""


def build_search_index() -> pd.DataFrame:
    """
    Build a searchable index by combining metadata, text stats, and extracted text.
    Returns a dataframe ready for review.
    """
    
    # Check if required CSVs exist
    if not os.path.exists(METADATA_CSV):
        raise FileNotFoundError(f"{METADATA_CSV} not found. Run graph_client.py first.")
    if not os.path.exists(TEXT_STATS_CSV):
        raise FileNotFoundError(f"{TEXT_STATS_CSV} not found. Run graph_client.py first.")
    
    logger.info(f"Reading {METADATA_CSV}...")
    meta_df = pd.read_csv(METADATA_CSV)
    
    logger.info(f"Reading {TEXT_STATS_CSV}...")
    text_df = pd.read_csv(TEXT_STATS_CSV)
    
    # Merge on file ID (unique per file on SharePoint)
    merged = meta_df.merge(text_df, on=["name", "id"], how="inner")
    logger.info(f"Merged {len(merged)} records from both CSVs")
    
    # Extract parent category and read text content
    records = []
    total_files = len(merged)
    for file_num, (idx, row) in enumerate(merged.iterrows(), start=1):
        filename = sanitize_filename(row.get("name", ""))
        print(f"({file_num}/{total_files}) Processing: {filename}")
        
        parent_path = row.get("parentPath_x") or row.get("parentPath_y")
        parent_id = row.get("parentId")  # parentId doesn't conflict, no _x/_y suffix
        local_path = row.get("local_path", "")
        
        category = extract_parent_category(parent_path)
        extracted_text = read_extracted_text(local_path) if local_path else ""
        
        # Use Graph API's direct webUrl - works best for public/anonymous access
        file_url = row.get("webUrl", "")
        
        record = {
            "filename": filename,
            "parent_category": category,
            "file_size_mb": row.get("size_mb"),
            "page_count": row.get("page_count"),
            "text_chars": row.get("text_chars"),
            "needs_ocr": row.get("needs_ocr"),
            "last_modified": row.get("lastModifiedDateTime"),
            "extraction_method": row.get("extraction_method", "pdfplumber"),  # Add extraction method
            "web_url": file_url,  # Direct SharePoint file URL
            "file_id": row.get("id"),
            "local_path": local_path,
            "extracted_text": extracted_text,
        }
        records.append(record)
    
    df = pd.DataFrame(records)
    
    # Log summary
    logger.info(f"Built index with {len(df)} records")
    logger.info(f"Categories found: {sorted(df['parent_category'].dropna().unique())}")
    logger.info(f"Missing category mapping: {(df['parent_category'].isna()).sum()} files")
    
    return df


def export_search_index(df: pd.DataFrame, output_path: str = OUTPUT_CSV):
    """
    Export the search index to CSV for review.
    Handles multiline text by:
    - Display version: excludes extracted text and local_path
    - Full version: uses tab-separated format and escapes newlines
    """
    # Create a display version without full text
    display_df = df.drop(columns=["extracted_text", "local_path"], errors="ignore")
    
    display_df.to_csv(output_path, index=False, encoding="utf-8")
    logger.info(f"Exported search index to {output_path}")
    
    # Also export full version but clean up text for CSV safety
    full_df = df.copy()
    # Replace newlines in extracted_text with spaces for CSV safety
    if "extracted_text" in full_df.columns:
        # Clean extracted text
        full_df["extracted_text"] = full_df["extracted_text"].apply(clean_extracted_text)
        # Create preview (first 500 chars)
        full_df["extracted_text_preview"] = full_df["extracted_text"].str[:500]
    
    # Keep essential columns with full text (not preview)
    # Include file_id for anonymous link generation
    full_df_export = full_df[["filename", "parent_category", "file_size_mb", "page_count", 
                               "text_chars", "extraction_method", "needs_ocr", "last_modified",
                               "web_url", "file_id", "extracted_text", "extracted_text_preview"]].copy()
    
    full_df_export.to_csv(output_path.replace(".csv", "_full.csv"), index=False, encoding="utf-8")
    logger.info(f"Exported full index (with full text) to {output_path.replace('.csv', '_full.csv')}")


def display_summary(df: pd.DataFrame):
    """
    Display summary statistics about the index.
    """
    print("\n" + "="*80)
    print("SEARCH INDEX SUMMARY")
    print("="*80)
    
    print(f"\nTotal PDFs indexed: {len(df)}")
    print(f"Total text characters: {df['text_chars'].sum():,}")
    print(f"Average file size: {df['file_size_mb'].mean():.2f} KB")
    print(f"Total file size: {df['file_size_mb'].sum():.2f} KB")
    
    print(f"\n--- By Category ---")
    cat_summary = df.groupby("parent_category", dropna=False).agg({
        "filename": "count",
        "text_chars": "sum",
        "file_size_mb": "sum",
    }).rename(columns={"filename": "count"})
    cat_summary = cat_summary.sort_values("count", ascending=False)
    print(cat_summary.to_string())
    
    print(f"\n--- OCR Needs ---")
    needs_ocr_count = df[df['needs_ocr'] == True].shape[0]
    print(f"Files that may need OCR (very little text): {needs_ocr_count}")
    if needs_ocr_count > 0:
        print("  Files:")
        for filename in df[df['needs_ocr'] == True]['filename'].values:
            print(f"    - {filename}")
    
    print(f"\n--- Unmapped Files (no parent category) ---")
    unmapped = df[df['parent_category'].isna()]
    if len(unmapped) > 0:
        print(f"Count: {len(unmapped)}")
        for filename in unmapped['filename'].values:
            print(f"  - {filename}")
    else:
        print("None! All files mapped successfully.")
    
    print("\n" + "="*80 + "\n")


def main():
    import time
    start_time = time.time()
    try:
        logger.info("Building search index from extracted PDFs...")
        df = build_search_index()
        
        logger.info(f"Exporting to {OUTPUT_CSV}...")
        export_search_index(df)
        
        display_summary(df)
        
        logger.info("Done! Review the exported CSVs before proceeding to Knack integration.")
        return df
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        raise
    finally:
        elapsed = time.time() - start_time
        minutes, seconds = divmod(elapsed, 60)
        print(f"\n{'='*80}")
        print(f"Processing completed in {int(minutes)}m {int(seconds)}s")
        print(f"{'='*80}")


if __name__ == "__main__":
    main()
