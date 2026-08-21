#!/usr/bin/env python3
"""Pure stdlib Python downloader for HuggingFace repositories.
No external dependencies required (no huggingface_hub, no pip packages).
"""
import sys
import os
import json
import urllib.request
import urllib.error

def download_hf_repo(repo_id, local_dir):
    print(f"Starting download of {repo_id} to {local_dir}...", flush=True)
    os.makedirs(local_dir, exist_ok=True)
    
    api_url = f"https://huggingface.co/api/models/{repo_id}"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Phosphene/1.0"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch model info from HuggingFace API: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
        
    siblings = data.get("siblings", [])
    total_files = len(siblings)
    print(f"Found {total_files} files to download for {repo_id}.", flush=True)
    
    for idx, sib in enumerate(siblings, 1):
        filename = sib.get("rfilename")
        if not filename or filename.startswith("."):
            continue
            
        file_url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        dest_path = os.path.join(local_dir, filename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            print(f"[{idx}/{total_files}] Already exists: {filename}", flush=True)
            continue
            
        print(f"[{idx}/{total_files}] Downloading {filename}...", flush=True)
        try:
            file_req = urllib.request.Request(file_url, headers={"User-Agent": "Phosphene/1.0"})
            with urllib.request.urlopen(file_req) as response, open(dest_path, "wb") as out_file:
                sharded_bytes = 0
                while True:
                    buffer = response.read(1024 * 1024)
                    if not buffer:
                        break
                    sharded_bytes += len(buffer)
                    out_file.write(buffer)
            print(f"[{idx}/{total_files}] Completed {filename}", flush=True)
        except Exception as e:
            print(f"Error downloading {filename}: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
            
    print("DOWNLOAD_COMPLETE", flush=True)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python hf_downloader.py <repo_id> <local_dir>")
        sys.exit(1)
    download_hf_repo(sys.argv[1], sys.argv[2])
