# Environment Setup Guide

This document explains how to set up the Python environment for the Oxbow Reentry PDF Indexing pipeline.

## Quick Start (Recommended)

### 1. Create a Virtual Environment

A virtual environment isolates project dependencies and prevents conflicts with other Python projects.

```bash
cd c:\Users\iaaw100a\VSCodeProjects\reentry-pdf-indexing

# Create a virtual environment called 'venv'
python -m venv venv

# Activate it
venv\Scripts\activate
```

**On activation, your command prompt will show `(venv)` at the beginning.**

### 2. Install Dependencies

Once the virtual environment is active, install all required packages:

```bash
pip install -r requirements.txt
```

This installs **only** the packages your project needs, in a contained environment.

### 3. Run the Pipeline

Now you can run the pipeline scripts. The virtual environment will automatically use the correct Python interpreter and packages:

```bash
# Activate venv if not already active
venv\Scripts\activate

# Run the pipeline
python sharepoint_graph_client.py
python build_search_index.py
python generate_anonymous_links.py
python knack_uploader.py
```

## Deactivating the Virtual Environment

When finished, deactivate the virtual environment:

```bash
deactivate
```

The prompt will return to normal (no `(venv)` prefix).

## Why Virtual Environments?

| Issue | Global Python | Virtual Environment |
|-------|---|---|
| **Package persistence** | ✅ Stays installed | ✅ Stays installed (in isolated `venv` folder) |
| **Version conflicts** | ❌ May conflict with other projects | ✅ Each project has its own versions |
| **Reinstalls** | ❌ Still needed if environment corrupts | ✅ Just delete `venv/` and recreate |
| **Portability** | ❌ Hard to share setup | ✅ Easy to share `requirements.txt` |
| **Cleanup** | ❌ Hard to remove | ✅ Just delete `venv/` folder |

## Troubleshooting

### "No module named 'pdfplumber'"
- **Cause**: Virtual environment not activated or not installed
- **Fix**: Run `venv\Scripts\activate` then `pip install -r requirements.txt`

### "pip is not recognized"
- **Cause**: Python not in PATH
- **Fix**: Use `python -m pip install -r requirements.txt`

### Virtual environment won't activate
- **Cause**: Execution policy issue on Windows
- **Fix**: Run PowerShell as Administrator, then:
  ```
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

### Large installation taking forever
- **Cause**: Torch (PyTorch) is large (~500MB). Only needed for OCR.
- **Expected**: First install takes 5-10 minutes. Subsequent installs are instant.

## Alternative: Global Python (Not Recommended)

If you prefer not to use a virtual environment:

```bash
pip install -r requirements.txt
```

⚠️ **Note**: This installs globally and packages will persist, but may cause conflicts with other projects.

## Checking Your Setup

Verify everything is installed correctly:

```bash
python -c "import msal, pdfplumber, easyocr, pandas; print('✓ All dependencies loaded')"
```

## Updating Dependencies

If new packages are needed in the future:

```bash
# Add to requirements.txt manually, then:
pip install -r requirements.txt

# OR update existing packages:
pip install --upgrade -r requirements.txt

# To save new versions:
pip freeze > requirements.txt
```
