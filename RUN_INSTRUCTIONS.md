# Pipeline Run Instructions - Quick Reference

## Current Status ✅

- **All 64 PDFs:** Retrieved from SharePoint
- **Text Extraction:** Complete with OCR for scanned docs
- **Search Index:** Built with all metadata + file IDs
- **Anonymous Links:** ✅ 64/64 (including the 3 special character files)
- **Knack Upload:** Complete - all 64 records live

## Standard Pipeline Run

Run these 4 commands in order:

```bash
# 1. Download PDFs and extract text from SharePoint
python sharepoint_graph_client.py

# 2. Build searchable index (includes file IDs)
python build_search_index.py

# 3. Generate anonymous sharing links
python generate_anonymous_links.py

# 4. Upload to Knack
python knack_uploader.py
```

**Typical runtime:** 5-10 minutes for 64 PDFs

## What Each Script Does

### 1️⃣ sharepoint_graph_client.py

- Authenticates to SharePoint via Graph API
- Downloads all PDFs from "Oxbow Reentry" folder
- Extracts text using:
  - **pdfplumber** for digital PDFs (fast)
  - **easyocr** for scanned documents (slower)
- **Creates:** `pdfs_metadata.csv`, `pdf_text_stats.csv`

### 2️⃣ build_search_index.py

- Merges metadata + text extracts
- Maps files to categories (from folder structure)
- **Creates:** `search_index_full.csv` with columns:
  - `filename` - PDF name
  - `file_id` - **CRITICAL for anonymous links**
  - `parent_category` - Category (Addiction Recovery, Employment, etc.)
  - `web_url` - Direct SharePoint URL (before anonymous)
  - `extracted_text` - Full text content
  - `extraction_method` - pdfplumber or easyocr

### 3️⃣ generate_anonymous_links.py

- Uses `file_id` to create "anyone with link" sharing
- Updates `web_url` in CSV to anonymous format:
  - From: `https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments/Shared%20Documents/...`
  - To: `https://wescaradatasolutions.sharepoint.com/:b:/s/OxbowReentryDocuments/IQAB...` (anonymous)
- **Key improvement:** Uses file IDs, so special characters don't break
- **If issues occur:** Run `fix_remaining_anonymous_links.py` for path-based recovery

### 4️⃣ knack_uploader.py

- Reads `search_index_full.csv` with anonymous URLs
- Deletes old records in Knack
- Creates new records with:
  - PDF name
  - Category
  - Searchable text
  - **Anonymous link** (users don't need SharePoint account)
- **Output:** 64 searchable PDF records live in Knack

## One-Time Setup Only

Do these once, then just run the pipeline above for updates:

1. Create `.env` with Azure credentials
2. Verify Azure AD service principal has `Sites.ReadWrite.All` permission
3. Configure SharePoint site URL and Knack API key
4. Test: `python test_sharepoint_connection.py`

## Partial Runs (Don't Repeat Unnecessary Steps)

### Just upload to Knack (keeping existing data)

```bash
# Example upload command
```

### Just generate anonymous links (keeping existing data)

```bash
# Example generate command
```

### Just refresh from SharePoint

```bash
# Example refresh command
```

## Troubleshooting

### Issue: "Failed to create anonymous link" for specific files

**Cause:** Special characters in filename

**Solution:**
```bash
# For path-based recovery
python fix_remaining_anonymous_links.py

# For folder-based recovery
python fix_bicycle_collective_link.py

# Then upload updated links
python knack_uploader.py
```

### Issue: "No matching category" errors

**Cause:** Folder doesn't match expected structure

**Solution:** Check folder structure under "Oxbow Reentry" in SharePoint. Update `extract_parent_category()` function in `build_search_index.py` if needed.

### Issue: Knack upload fails

**Cause:** API key invalid or network issue

**Solution:**
```bash
# Verify connection
python test_sharepoint_connection.py

# Check Knack API credentials in .env
# Verify object_14 exists in Knack with correct fields
```

## File Dependencies

```
sharepoint_graph_client.py
    ↓ Creates: pdfs_metadata.csv, pdf_text_stats.csv, downloads/
    ↓
build_search_index.py
    ↓ Reads: pdfs_metadata.csv, pdf_text_stats.csv, downloads/
    ↓ Creates: search_index.csv, search_index_full.csv
    ↓
generate_anonymous_links.py
    ↓ Reads: search_index_full.csv
    ↓ Updates: search_index_full.csv (web_url field)
    ↓
knack_uploader.py
    ↓ Reads: search_index_full.csv (final version with anonymous URLs)
    ↓ Creates: 64 records in Knack
```

## Important: File IDs

The `file_id` column is **critical** for anonymous link generation:

- ✅ Extracted by `sharepoint_graph_client.py` in metadata
- ✅ Preserved by `build_search_index.py` (after fix to line 355)
- ✅ Used by `generate_anonymous_links.py` for all files
- ✅ Supports special characters (apostrophes, dashes, etc.)

If you see "No file ID available", check that `build_search_index.py` is outputting the `file_id` column.

## Performance Notes

### Category naming

The SharePoint folder `Courts (Navigating Court Cases)` maps to the existing
Knack category `Courts (How to Navigate Court Cases)`. Keep that original category;
do not create a second Courts category to match the folder name. Category-link
repair resolves the original category name back to the actual SharePoint folder.

- **Download + Extract:** ~3 min for 64 PDFs (depends on file sizes)
- **Index Building:** ~1-2 min (sequential processing)
- **Anonymous Link Generation:** ~1 min (with 0.5s rate limiting per file)
- **Knack Upload:** ~20 sec (batch creation)

**Total:** ~5-10 minutes for full pipeline

## Next Steps

### For New PDFs

### For Updates to Existing PDFs

### For Maintenance

### For Monitoring

- All 64 records present
