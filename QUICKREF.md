# SharePoint Quick Reference

## One-Liner Setup
```bash
# 1. Update .env with SharePoint settings (copy from .env.example)
# 2. Test connection
python test_sharepoint_connection.py

# 3. If test passes, run the pipeline:
python sharepoint_graph_client.py && python build_search_index.py && python knack_uploader.py
```

## Required .env Values

```ini
# Azure App (same as before)
TENANT_ID=<from Azure Portal>
CLIENT_ID=<from Azure Portal>
CLIENT_SECRET=<from Azure Portal>

# SharePoint Site Details (NEW)
SHAREPOINT_SITE_URL=https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments
SHAREPOINT_SITE_NAME=OxbowReentryDocuments
SHAREPOINT_DRIVE_NAME=Shared Documents

# Knack (same as before)
KNACK_API_KEY=<from Knack>
KNACK_APP_ID=<from Knack>

# Optional
EXTRACT_TEXT=false    # Set to true to download and extract PDF text
SAVE_CSV=true         # Save metadata CSV
```

## File Reference

| File | Purpose | When to Run |
|------|---------|-----------|
| `test_sharepoint_connection.py` | Verify SharePoint access | **First** - before anything else |
| `sharepoint_graph_client.py` | Get PDFs from SharePoint | Every time you want fresh data |
| `build_search_index.py` | Combine CSVs into search index | After sharepoint_graph_client.py |
| `knack_uploader.py` | Upload to Knack database | After build_search_index.py |

## Common Issues & Fixes

| Issue | Check | Fix |
|-------|-------|-----|
| "Site not found" | SHAREPOINT_SITE_URL | Verify exact URL from SharePoint |
| "Drive not found" | SHAREPOINT_DRIVE_NAME | Run test script to see available drives |
| "Access denied" | App permissions | Verify Sites.Read.All + Files.Read.All with Admin consent |
| "Token failed" | CLIENT_ID, CLIENT_SECRET | Double-check credentials in Azure Portal |

## API Permissions Needed

In Azure Portal → App Registration → API Permissions:
- ✓ Microsoft Graph → Application permissions
- ✓ Sites.Read.All
- ✓ Files.Read.All
- ✓ Admin consent granted

## Environment Variables Explained

**SHAREPOINT_SITE_URL**
- The full URL to your SharePoint site
- Example: `https://wescaradatasolutions.sharepoint.com/sites/OxbowReentryDocuments`
- Where: SharePoint → Site settings → copy the site URL

**SHAREPOINT_SITE_NAME**
- The site identifier used in Graph API
- Example: `OxbowReentryDocuments` (the part after `/sites/`)

**SHAREPOINT_DRIVE_NAME**
- The document library name within the site
- Usually "Shared Documents" or "Documents"
- Check by running `test_sharepoint_connection.py` to see available libraries

**TARGET_FOLDER_PATH**
- The folder path relative to the document library root
- Example: `Oxbow Reentry` (this is a folder inside the library)

## What Changed?

| Aspect | Before (OneDrive) | After (SharePoint) |
|--------|------------|-----------|
| Retrieval Script | `graph_client.py` | `sharepoint_graph_client.py` |
| Auth Method | Same (app-only) | Same (app-only) |
| Credentials | TENANT_ID, CLIENT_ID, CLIENT_SECRET | TENANT_ID, CLIENT_ID, CLIENT_SECRET |
| Extra Parameter | USER_UPN | Not needed |
| File Links | OneDrive URLs | SharePoint URLs |
| Knack Field | FIELD_ONEDRIVE_URL | FIELD_ONEDRIVE_URL (same field) |

## Scripts Unchanged

- `build_search_index.py` ← Same as before
- `knack_uploader.py` ← Works with both OneDrive and SharePoint URLs
- All other utilities ← No changes

---

**Next Step:** Run `python test_sharepoint_connection.py` to verify your setup!
