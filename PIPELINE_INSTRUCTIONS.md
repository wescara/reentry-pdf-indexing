# Oxbow Reentry PDF Indexing Pipeline

## Overview

This pipeline migrates PDF documents from OneDrive to SharePoint, generates anonymous sharing links, and syncs searchable content to Knack.

**Current Status:** ✅ Complete - All 64 PDFs indexed and uploaded to Knack

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. RETRIEVE: sharepoint_graph_client.py                        │
│    - Authenticates to Microsoft Graph (app-only)               │
│    - Enumerates all PDFs from SharePoint                       │
│    - Extracts text (pdfplumber + easyocr for scans)           │
│    - Outputs: pdfs_metadata.csv, pdf_text_stats.csv            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. INDEX: build_search_index.py                                │
│    - Merges metadata + text extracts                           │
│    - Extracts parent category from folder structure            │
│    - Includes file IDs for anonymous link generation           │
│    - Outputs: search_index_full.csv (with file_id column)      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. SHARE: generate_anonymous_links.py                          │
│    - Uses file IDs to create "anyone with link" sharing links  │
│    - Bypasses account requirement for Knack users              │
│    - Updates search_index_full.csv with anonymous URLs         │
│                                                                 │
│    If special characters cause issues:                         │
│    - fix_remaining_anonymous_links.py (path-based lookup)      │
│    - fix_bicycle_collective_link.py (folder browsing)          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. UPLOAD: knack_uploader.py                                   │
│    - Reads search_index_full.csv with anonymous URLs           │
│    - Creates/replaces records in Knack database                │
│    - Makes PDFs searchable to all users (no accounts needed)    │
│    - Outputs: 64 searchable PDF records in Knack               │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

### 1. Environment Setup

Create a `.env` file in project root:

```bash
# Azure AD Service Principal
TENANT_ID=your-tenant-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret

# SharePoint Configuration
SHAREPOINT_SITE_URL=https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments
SHAREPOINT_SITE_NAME=OxbowReentryDocuments
SHAREPOINT_DRIVE_NAME=Oxbow Reentry Documents

# Target Folder
TARGET_FOLDER_PATH=Oxbow Reentry
TARGET_FOLDER_NAME=Oxbow Reentry

# Local Configuration
DOWNLOAD_DIR=downloads
EXTRACT_TEXT=true

# Knack API
KNACK_API_URL=https://api.knack.com/v1
KNACK_API_KEY=your-knack-api-key
KNACK_APP_ID=your-knack-app-id
```

### 2. Azure AD Service Principal

Required permissions in Azure:
- `Sites.Read.All` - Read SharePoint sites
- `Files.Read.All` - Read files
- **`Sites.ReadWrite.All`** - Create anonymous sharing links (essential)

### 3. Python Dependencies

```bash
pip install msal requests python-dotenv pdfplumber easyocr pillow numpy pandas
```

### 4. Knack Setup

- Have a Knack app with a "Reentry" object configured
- Create a view/records for the object
- Get KNACK_API_KEY from Knack account settings

## Running the Pipeline

### Option 1: Full Pipeline (All Steps)

```bash
# Step 1: Retrieve from SharePoint
python sharepoint_graph_client.py

# Step 2: Build search index
python build_search_index.py

# Step 3: Generate anonymous links
python generate_anonymous_links.py

# Step 4: Upload to Knack
python knack_uploader.py
```

**Total time:** ~5-10 minutes for 64 PDFs

### Option 2: Individual Steps

Run only the steps needed for your use case:

```bash
# Just update Knack with existing search index
python knack_uploader.py

# Generate new anonymous links for current PDFs
python generate_anonymous_links.py

# Refresh data from SharePoint
python sharepoint_graph_client.py && python build_search_index.py
```

### Option 3: Fixing Special Character Issues

If files with special characters (apostrophes, dashes, etc.) fail to get anonymous links:

```bash
# For files with path-based special characters
python fix_remaining_anonymous_links.py

# For files found via folder listing
python fix_bicycle_collective_link.py
```

## Output Files

| File | Purpose | Columns |
|------|---------|---------|
| `pdfs_metadata.csv` | File metadata from SharePoint | name, size_mb, lastModifiedDateTime, webUrl, **id**, parentPath, parentId |
| `pdf_text_stats.csv` | Text extraction statistics | name, page_count, text_chars, extraction_method, needs_ocr, local_path |
| `search_index_full.csv` | **Final searchable index (used by Knack)** | filename, parent_category, file_size_mb, page_count, text_chars, extraction_method, needs_ocr, last_modified, web_url, **file_id**, extracted_text, extracted_text_preview |

## Key Features

### 1. File ID Preservation ✅

The `file_id` column is now included in `search_index_full.csv` to support:
- Direct file ID lookups (avoids special character issues)
- Anonymous link generation for all files
- Robust error recovery

### 2. Anonymous Link Generation ✅

All 64 PDFs have "anyone with link" sharing enabled:
- No SharePoint account required to view
- Links work for external users
- Knack users can access PDFs directly

### 3. Text Extraction

Two extraction methods:
- **pdfplumber** - Fast, accurate for digital PDFs
- **easyocr** - For scanned documents (detected automatically)

### 4. Category Mapping

Files are automatically categorized based on folder structure:
- `Addiction Recovery`
- `Bicycles`
- `Cell Phones and Computers`
- `Church Incarceration Resources`
- (17 categories total)

## Troubleshooting

### Problem: "No file ID available, skipping"

**Cause:** File ID not in CSV  
**Solution:** Ensure `build_search_index.py` is outputting the `file_id` column ✓

### Problem: "Failed to create anonymous link"

**Cause:** 
1. File has special characters → Use `fix_remaining_anonymous_links.py`
2. Permission issue → Verify `Sites.ReadWrite.All` is granted in Azure

**Solution:**
```bash
# For specific problem files
python fix_remaining_anonymous_links.py
python fix_bicycle_collective_link.py
```

### Problem: "No file path found" during text extraction

**Cause:** PDF wasn't downloaded  
**Solution:** Run `sharepoint_graph_client.py` first to download PDFs

### Problem: Knack upload fails

**Cause:** API key invalid or app structure mismatch  
**Solution:**
```bash
# Test connection
python -c "from knack_uploader import test_knack_connection; test_knack_connection()"
```

## Verification Checklist

- [ ] All 64 PDFs have file IDs in `search_index_full.csv`
- [ ] All 64 PDFs have anonymous sharing links (URLs contain `/:b:/`)
- [ ] All 64 PDFs uploaded to Knack
- [ ] Can view PDFs in Knack without SharePoint account
- [ ] Search works for all categories

## Next Steps

### For New PDFs
1. Add PDFs to SharePoint folder
2. Run `python sharepoint_graph_client.py`
3. Run `python build_search_index.py`
4. Run `python generate_anonymous_links.py`
5. Run `python knack_uploader.py`

### For Maintenance
- Monthly: Re-run pipeline to pick up new/updated files
- Quarterly: Verify all links still work (some may expire)
- As-needed: Update Knack field mappings if schema changes

## Architecture Notes

### Why SharePoint?
- Better permission model than OneDrive
- Supports app-only authentication
- More reliable for batch operations
- Easier anonymous link management

### Why Anonymous Links?
- Knack users don't have SharePoint accounts
- External stakeholders can access PDFs
- No maintenance of access lists
- Secure: "anyone with link" only (not public)

### Why File IDs?
- Unique identifier independent of filename
- Supports special characters (apostrophes, dashes, etc.)
- Required for Graph API anonymous link creation
- Prevents search issues with Unicode characters

## Support

For issues or questions:
1. Check troubleshooting section above
2. Run diagnostic: `python test_sharepoint_connection.py`
3. Review logs in console output
4. Verify .env configuration
