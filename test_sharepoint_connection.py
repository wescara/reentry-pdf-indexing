#!/usr/bin/env python3
"""
Quick test script to verify SharePoint connection and permissions.
Run this before the main pipeline to ensure everything is set up correctly.
"""

import os
import sys
from dotenv import load_dotenv, find_dotenv

# Load environment
load_dotenv(find_dotenv())

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")
SHAREPOINT_SITE_NAME = os.getenv("SHAREPOINT_SITE_NAME")
SHAREPOINT_DRIVE_NAME = os.getenv("SHAREPOINT_DRIVE_NAME", "Shared Documents")

def check_env_vars():
    """Verify all required environment variables are set."""
    print("=" * 60)
    print("1. CHECKING ENVIRONMENT VARIABLES")
    print("=" * 60)
    
    vars_to_check = {
        "TENANT_ID": TENANT_ID,
        "CLIENT_ID": CLIENT_ID,
        "CLIENT_SECRET": "***hidden***" if CLIENT_SECRET else None,
        "SHAREPOINT_SITE_URL": SHAREPOINT_SITE_URL,
        "SHAREPOINT_SITE_NAME": SHAREPOINT_SITE_NAME,
        "SHAREPOINT_DRIVE_NAME": SHAREPOINT_DRIVE_NAME,
    }
    
    all_set = True
    for key, value in vars_to_check.items():
        if value:
            print(f"✓ {key}: {value}")
        else:
            print(f"✗ {key}: MISSING")
            all_set = False
    
    if not all_set:
        print("\n❌ Some environment variables are missing!")
        print("   Update your .env file and try again.")
        return False
    
    print("\n✓ All environment variables are set!\n")
    return True


def test_token_acquisition():
    """Test if we can get an access token."""
    print("=" * 60)
    print("2. TESTING TOKEN ACQUISITION")
    print("=" * 60)
    
    try:
        from msal import ConfidentialClientApplication
        
        authority = f"https://login.microsoftonline.com/{TENANT_ID}"
        app = ConfidentialClientApplication(
            CLIENT_ID,
            authority=authority,
            client_credential=CLIENT_SECRET,
        )
        
        print(f"Connecting to: {authority}")
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        
        if "access_token" in result:
            token = result["access_token"]
            print(f"✓ Token acquired successfully!")
            print(f"  Token (first 50 chars): {token[:50]}...")
            return token
        else:
            print(f"✗ Token acquisition failed!")
            print(f"  Error: {result.get('error')}")
            print(f"  Description: {result.get('error_description')}")
            return None
            
    except Exception as e:
        print(f"✗ Exception during token acquisition: {e}")
        return None


def test_sharepoint_access(token):
    """Test if we can access the SharePoint site."""
    print("\n" + "=" * 60)
    print("3. TESTING SHAREPOINT SITE ACCESS")
    print("=" * 60)
    
    try:
        import requests
        from urllib.parse import urlparse
        
        # Parse the SharePoint site URL
        parsed = urlparse(SHAREPOINT_SITE_URL)
        hostname = parsed.hostname
        path = parsed.path.strip("/")
        site_id_url_format = f"{hostname}:/{path}"
        
        print(f"Site URL: {SHAREPOINT_SITE_URL}")
        print(f"Looking up site ID...")
        
        # Try to get site info using URL format
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id_url_format}"
        
        print(f"Requesting: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            site_data = response.json()
            print(f"✓ Connected to site: {site_data.get('displayName')}")
            actual_site_id = site_data.get('id')
            print(f"  Actual Site ID: {actual_site_id}")
            return actual_site_id
        elif response.status_code == 401:
            print(f"✗ Authentication failed (401)")
            print(f"  Check your CLIENT_ID and CLIENT_SECRET")
            return None
        elif response.status_code == 403:
            print(f"✗ Access denied (403)")
            print(f"  Your app may not have Sites.Read.All permission")
            return None
        elif response.status_code == 404:
            print(f"✗ Site not found (404)")
            print(f"  Check your SHAREPOINT_SITE_URL")
            return None
        else:
            print(f"✗ Unexpected error ({response.status_code})")
            print(f"  Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"✗ Exception: {e}")
        return None


def test_drive_access(token, site_id):
    """Test if we can access the document library (drive)."""
    print("\n" + "=" * 60)
    print("4. TESTING DOCUMENT LIBRARY ACCESS")
    print("=" * 60)
    
    try:
        import requests
        
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
        
        print(f"Looking for drive: '{SHAREPOINT_DRIVE_NAME}'")
        print(f"Requesting: {url}")
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            drives = data.get("value", [])
            
            if not drives:
                print(f"✗ No drives found in site")
                return False
            
            # Look for the matching drive
            drive_name_lower = SHAREPOINT_DRIVE_NAME.lower()
            matching_drive = None
            
            print(f"\nAvailable drives:")
            for drive in drives:
                name = drive.get("name")
                drive_id = drive.get("id")
                is_match = name.lower() == drive_name_lower
                marker = "👉" if is_match else "  "
                print(f"{marker} {name} (ID: {drive_id})")
                
                if is_match:
                    matching_drive = drive
            
            if matching_drive:
                print(f"\n✓ Found document library: {matching_drive.get('name')}")
                return True
            else:
                print(f"\n✗ Drive '{SHAREPOINT_DRIVE_NAME}' not found")
                print(f"   Please use one of the drive names listed above")
                return False
        else:
            print(f"✗ Error ({response.status_code})")
            print(f"  Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"✗ Exception: {e}")
        return None


def main():
    print("\n🔍 SharePoint Connection Test\n")
    
    # Step 1: Check environment
    if not check_env_vars():
        sys.exit(1)
    
    # Step 2: Get token
    token = test_token_acquisition()
    if not token:
        sys.exit(1)
    
    # Step 3: Test site access
    site_id = test_sharepoint_access(token)
    if not site_id:
        sys.exit(1)
    
    # Step 4: Test drive access
    if not test_drive_access(token, site_id):
        sys.exit(1)
    
    # Success!
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)
    print("\nYou're ready to run sharepoint_graph_client.py")
    print("  python sharepoint_graph_client.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
