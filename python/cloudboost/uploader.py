"""
cloud-boost core uploader
Handles: chunking, compression, parallel upload, resume
"""

import os
import gzip
import math
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
from colorama import Fore, Style, init

init(autoreset=True)

# ─── Constants ────────────────────────────────────────────────────────────────
DEFAULT_CHUNK_SIZE_MB = 5
DEFAULT_THREADS = 6
CHECKPOINT_EXT = ".cbcheckpoint"


def _chunk_file(filepath: str, chunk_size_mb: int) -> list[str]:
    """Split a file into chunks. Returns list of chunk file paths."""
    chunk_size = chunk_size_mb * 1024 * 1024
    chunks = []
    chunk_dir = Path(filepath).parent / f".cb_chunks_{Path(filepath).stem}"
    chunk_dir.mkdir(exist_ok=True)

    with open(filepath, "rb") as f:
        index = 0
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            chunk_path = chunk_dir / f"chunk_{index:04d}.bin"
            with open(chunk_path, "wb") as cf:
                cf.write(data)
            chunks.append(str(chunk_path))
            index += 1

    return chunks


def _compress_chunk(chunk_path: str) -> str:
    """Gzip compress a chunk. Returns compressed file path."""
    compressed_path = chunk_path + ".gz"
    with open(chunk_path, "rb") as f_in:
        with gzip.open(compressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    return compressed_path


def _upload_chunk_rclone(chunk_path: str, destination: str, chunk_index: int) -> bool:
    """Upload a single chunk to destination via rclone."""
    dest_path = f"{destination}/chunks/chunk_{chunk_index:04d}.gz"
    result = subprocess.run(
        ["rclone", "copyto", chunk_path, dest_path, "--progress"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _save_checkpoint(filepath: str, uploaded_chunks: list[int]) -> None:
    """Save upload progress checkpoint for resume support."""
    checkpoint_path = filepath + CHECKPOINT_EXT
    with open(checkpoint_path, "w") as f:
        json.dump({"uploaded_chunks": uploaded_chunks}, f)


def _load_checkpoint(filepath: str) -> list[int]:
    """Load existing checkpoint if available."""
    checkpoint_path = filepath + CHECKPOINT_EXT
    if Path(checkpoint_path).exists():
        with open(checkpoint_path) as f:
            data = json.load(f)
            return data.get("uploaded_chunks", [])
    return []


def _cleanup(filepath: str) -> None:
    """Remove temp chunks and checkpoint after successful upload."""
    chunk_dir = Path(filepath).parent / f".cb_chunks_{Path(filepath).stem}"
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir)
    checkpoint = filepath + CHECKPOINT_EXT
    if Path(checkpoint).exists():
        os.remove(checkpoint)


def upload(
    filepath: str,
    destination: str,
    threads: int = DEFAULT_THREADS,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
    compress: bool = True,
) -> bool:
    """
    Main upload function.
    Splits file → compresses chunks → uploads in parallel → cleans up.
    """
    if not Path(filepath).exists():
        print(f"{Fore.RED}✗ File not found: {filepath}")
        return False

    file_size_mb = Path(filepath).stat().st_size / (1024 * 1024)
    print(f"\n{Fore.CYAN}⚡ cloud-boost starting upload")
    print(f"   File     : {filepath} ({file_size_mb:.1f} MB)")
    print(f"   Target   : {destination}")
    print(f"   Threads  : {threads}")
    print(f"   Chunk    : {chunk_size_mb} MB")
    print(f"   Compress : {'yes' if compress else 'no'}\n")

    # Step 1 — Split
    print(f"{Fore.YELLOW}→ Splitting into chunks...")
    chunks = _chunk_file(filepath, chunk_size_mb)
    print(f"  {len(chunks)} chunks created\n")

    # Step 2 — Compress
    if compress:
        print(f"{Fore.YELLOW}→ Compressing chunks...")
        compressed = []
        for c in tqdm(chunks, desc="  Compressing", unit="chunk"):
            compressed.append(_compress_chunk(c))
        chunks = compressed
        print()

    # Step 3 — Resume check
    already_uploaded = _load_checkpoint(filepath)
    if already_uploaded:
        print(f"{Fore.MAGENTA}→ Resuming — {len(already_uploaded)} chunks already done\n")

    # Step 4 — Parallel upload
    print(f"{Fore.YELLOW}→ Uploading {len(chunks)} chunks with {threads} parallel threads...")
    uploaded = list(already_uploaded)
    failed = []

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(_upload_chunk_rclone, chunk, destination, i): i
            for i, chunk in enumerate(chunks)
            if i not in already_uploaded
        }

        with tqdm(total=len(futures), desc="  Uploading", unit="chunk") as bar:
            for future in as_completed(futures):
                chunk_index = futures[future]
                success = future.result()
                if success:
                    uploaded.append(chunk_index)
                    _save_checkpoint(filepath, uploaded)
                else:
                    failed.append(chunk_index)
                bar.update(1)

    # Step 5 — Result
    if failed:
        print(f"\n{Fore.RED}✗ {len(failed)} chunks failed. Run with --resume to retry.")
        return False

    _cleanup(filepath)
    total = len(chunks)
    print(f"\n{Fore.GREEN}✓ Upload complete! {total}/{total} chunks uploaded successfully.")
    print(f"{Fore.GREEN}  Destination: {destination}\n")
    return True
