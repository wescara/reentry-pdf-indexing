import argparse
import os
import sys
import subprocess

def run_script(script, args=None):
    cmd = [sys.executable, script]
    if args:
        cmd += args
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Error running {script}")
        sys.exit(result.returncode)

def main():
    parser = argparse.ArgumentParser(description="Run the full PDF indexing pipeline.")
    parser.add_argument('--target_folder_path', type=str, default=os.getenv('TARGET_FOLDER_PATH', 'Oxbow Reentry'), help='Target folder path for SharePoint download')
    parser.add_argument('--target_folder_name', type=str, default=os.getenv('TARGET_FOLDER_NAME', 'Oxbow Reentry'), help='Target folder name for SharePoint search fallback')
    parser.add_argument('--download_dir', type=str, default=os.getenv('DOWNLOAD_DIR', 'downloads'), help='Directory to save downloaded PDFs')
    parser.add_argument('--save_csv', type=str, default=os.getenv('SAVE_CSV', 'true'), help='Whether to save CSV outputs')
    parser.add_argument('--extract_text', type=str, default=os.getenv('EXTRACT_TEXT', 'true'), help='Whether to extract text from PDFs')
    parser.add_argument('--env_file', type=str, default='.env', help='Path to .env file')
    args = parser.parse_args()

    # Optionally set environment variables for subprocesses
    os.environ['TARGET_FOLDER_PATH'] = args.target_folder_path
    os.environ['TARGET_FOLDER_NAME'] = args.target_folder_name
    os.environ['DOWNLOAD_DIR'] = args.download_dir
    os.environ['SAVE_CSV'] = args.save_csv
    os.environ['EXTRACT_TEXT'] = args.extract_text

    # Run each pipeline step
    print("Step 1: Download PDFs and extract text from SharePoint")
    run_script('sharepoint_graph_client.py')

    print("Step 2: Build searchable index")
    run_script('build_search_index.py')

    print("Step 3: Generate anonymous sharing links")
    run_script('generate_anonymous_links.py')

    print("Step 4: Upload to Knack")
    run_script('knack_uploader.py')

    print("Pipeline completed successfully.")

if __name__ == "__main__":
    main()
