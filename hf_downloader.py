#!/usr/bin/env python3
"""Pure stdlib Python downloader for HuggingFace repositories.
No external dependencies required (no huggingface_hub, no pip packages).
Features:
- Resumable file downloads (HTTP Range)
- Real-time live percentage & speed progress updates in stdout
- Atomic partial file staging (.partial)
- Automatic retry on transient network drops
"""
import sys
import os
import time
import json
import urllib.request
import urllib.error

CHUNK_SIZE = 1024 * 512  # 512 KB

def download_hf_repo(repo_id, local_dir):
    print(f"Starting download of {repo_id} to {local_dir}...", flush=True)
    os.makedirs(local_dir, exist_ok=True)
    
    api_url = f"https://huggingface.co/api/models/{repo_id}"
    req = urllib.request.Request(api_url, headers={"User-Agent": "MacStudioMLX/1.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch model info from HuggingFace API: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
        
    siblings = [s for s in data.get("siblings", []) if s.get("rfilename") and not s.get("rfilename").startswith(".")]
    total_files = len(siblings)
    print(f"Found {total_files} files to download for {repo_id}.", flush=True)
    
    for idx, sib in enumerate(siblings, 1):
        filename = sib.get("rfilename")
        file_url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        dest_path = os.path.join(local_dir, filename)
        partial_path = dest_path + ".partial"
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Check if already complete
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            sz_mb = round(os.path.getsize(dest_path) / (1024 * 1024), 1)
            print(f"[{idx}/{total_files}] Already exists: {filename} ({sz_mb} MB) - 100%", flush=True)
            continue
            
        # Download with retry and range resumption
        max_retries = 5
        success = False
        
        for attempt in range(1, max_retries + 1):
            have_bytes = os.path.getsize(partial_path) if os.path.exists(partial_path) else 0
            headers = {"User-Agent": "MacStudioMLX/1.0"}
            if have_bytes > 0:
                headers["Range"] = f"bytes={have_bytes}-"
                
            file_req = urllib.request.Request(file_url, headers=headers)
            
            try:
                with urllib.request.urlopen(file_req, timeout=30) as response:
                    is_range = (response.status == 206)
                    content_length = response.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        cl = int(content_length)
                        total_bytes = (cl + have_bytes) if is_range else cl
                    else:
                        total_bytes = 0
                        
                    mode = "ab" if (is_range and have_bytes > 0) else "wb"
                    if mode == "wb":
                        have_bytes = 0
                        
                    last_print = time.time()
                    start_time = time.time()
                    bytes_since_start = 0
                    
                    with open(partial_path, mode) as out_file:
                        while True:
                            buffer = response.read(CHUNK_SIZE)
                            if not buffer:
                                break
                            out_file.write(buffer)
                            have_bytes += len(buffer)
                            bytes_since_start += len(buffer)
                            
                            now = time.time()
                            if now - last_print >= 1.0:
                                elapsed = now - start_time
                                speed_mb = (bytes_since_start / (1024 * 1024)) / max(elapsed, 0.001)
                                curr_mb = have_bytes / (1024 * 1024)
                                
                                if total_bytes > 0:
                                    pct = min(100.0, (have_bytes / total_bytes) * 100.0)
                                    tot_mb = total_bytes / (1024 * 1024)
                                    print(f"[{idx}/{total_files}] {filename}: {curr_mb:.1f} / {tot_mb:.1f} MB ({pct:.1f}%) · {speed_mb:.1f} MB/s", flush=True)
                                else:
                                    print(f"[{idx}/{total_files}] {filename}: {curr_mb:.1f} MB · {speed_mb:.1f} MB/s", flush=True)
                                last_print = now
                                
                # Rename partial to dest
                if os.path.exists(partial_path):
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    os.rename(partial_path, dest_path)
                    
                total_mb = round(os.path.getsize(dest_path) / (1024 * 1024), 1)
                print(f"[{idx}/{total_files}] Completed {filename} ({total_mb} MB) (100%)", flush=True)
                success = True
                break
                
            except urllib.error.HTTPError as e:
                if e.code == 416:  # Range not satisfiable (already fully downloaded)
                    if os.path.exists(partial_path):
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                        os.rename(partial_path, dest_path)
                    success = True
                    break
                print(f"[{idx}/{total_files}] HTTP Error {e.code} on {filename} (attempt {attempt}/{max_retries}): {e}", file=sys.stderr, flush=True)
                time.sleep(2 * attempt)
            except Exception as e:
                print(f"[{idx}/{total_files}] Connection issue on {filename} (attempt {attempt}/{max_retries}): {e}", file=sys.stderr, flush=True)
                time.sleep(2 * attempt)
                
        if not success:
            print(f"Error: Failed to download {filename} after {max_retries} attempts.", file=sys.stderr, flush=True)
            sys.exit(1)
            
    print("DOWNLOAD_COMPLETE", flush=True)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python hf_downloader.py <repo_id> <local_dir>")
        sys.exit(1)
    download_hf_repo(sys.argv[1], sys.argv[2])
