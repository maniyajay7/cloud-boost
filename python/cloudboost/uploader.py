"""
cloud-boost core uploader v2
Production-grade: streaming pipeline, 100GB+ support, integrity verification
"""

import os
import io
import gzip
import math
import time
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from colorama import Fore, Style, init

from cloudboost.config import UploadConfig, format_size, format_duration, MB, GB, ensure_dirs
from cloudboost.integrity import (
    compute_sha256,
    compute_chunk_hash,
    load_manifest,
    save_manifest,
    clear_manifest,
    is_chunk_uploaded,
    mark_chunk_uploaded,
    file_changed,
)
from cloudboost.transport import (
    check_rclone_installed,
    get_rclone_remotes,
    upload_chunk as rclone_upload_chunk,
    upload_direct_rclone,
    upload_reassembly_manifest,
)
from cloudboost.logger import get_logger, log_with_data

init(autoreset=True)


def _stream_compress_chunk(filepath: str, offset: int, size: int, level: int) -> tuple[bytes, str]:
    """
    Read a chunk from file at offset, compress it in memory, return (compressed_bytes, raw_hash).
    STREAMING: never writes raw chunk to disk — only compressed result touches disk.
    """
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw_data = f.read(size)

    raw_hash = compute_chunk_hash(raw_data)

    # Compress in memory
    compressed = gzip.compress(raw_data, compresslevel=level)

    return compressed, raw_hash


def _process_and_upload_chunk(
    filepath: str,
    chunk_index: int,
    offset: int,
    chunk_size: int,
    destination: str,
    config: UploadConfig,
    manifest: dict,
) -> dict:
    """
    Process a single chunk: read → compress → write temp → upload → delete temp.
    Returns result dict with status and metrics.
    """
    result = {
        "chunk_index": chunk_index,
        "success": False,
        "skipped": False,
        "raw_size": 0,
        "compressed_size": 0,
        "upload_time": 0,
    }

    try:
        # Step 1 — Stream read + compress in memory
        compressed_data, raw_hash = _stream_compress_chunk(
            filepath, offset, chunk_size, config.compression_level
        )
        result["raw_size"] = chunk_size
        result["compressed_size"] = len(compressed_data)

        # Step 2 — Check if already uploaded (dedup + resume)
        if config.dedup and is_chunk_uploaded(manifest, chunk_index, raw_hash):
            result["success"] = True
            result["skipped"] = True
            return result

        # Step 3 — Write compressed chunk to temp file (only compressed touches disk)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"cb_{chunk_index:06d}.gz")

        with open(temp_path, "wb") as f:
            f.write(compressed_data)

        # Free compressed data from memory immediately
        del compressed_data

        # Step 4 — Upload via rclone
        start_time = time.monotonic()
        success = rclone_upload_chunk(temp_path, destination, chunk_index, config)
        result["upload_time"] = time.monotonic() - start_time

        if success:
            mark_chunk_uploaded(manifest, chunk_index, raw_hash, result["compressed_size"])
            save_manifest(filepath, manifest)
            result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    finally:
        # Always clean up temp file
        temp_path = os.path.join(tempfile.gettempdir(), f"cb_{chunk_index:06d}.gz")
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return result


def upload_file(
    filepath: str,
    destination: str,
    config: UploadConfig | None = None,
) -> bool:
    """
    Main upload entry point. Handles single file upload with:
    - Auto-tuned config based on file size
    - Streaming compression (no raw chunk disk writes)
    - Parallel upload with configurable threads
    - SHA-256 integrity verification
    - Resume from checkpoint
    - Deduplication
    - Progress bar with ETA
    """
    logger = get_logger("cloudboost", verbose=config.verbose if config else False)
    ensure_dirs()

    # ─── Validations ──────────────────────────────────────────────────────────
    if not Path(filepath).exists():
        logger.error(f"File not found: {filepath}")
        return False

    if not Path(filepath).is_file():
        logger.error(f"Not a file: {filepath} (use upload_folder for directories)")
        return False

    if not check_rclone_installed():
        logger.error("rclone is not installed. Install from https://rclone.org/install/")
        return False

    file_size = Path(filepath).stat().st_size
    if file_size == 0:
        logger.error("File is empty — nothing to upload")
        return False

    # ─── Auto-tune config ─────────────────────────────────────────────────────
    if config is None:
        config = UploadConfig.auto_tune(file_size)
    else:
        # Re-tune with user overrides preserved
        auto = UploadConfig.auto_tune(file_size)
        if config.threads == 6:  # still default → use auto
            config.threads = auto.threads
        if config.chunk_size == 8 * MB:  # still default → use auto
            config.chunk_size = auto.chunk_size

    # ─── Small file fast path ─────────────────────────────────────────────────
    if file_size < 50 * MB:
        logger.info(f"Small file ({format_size(file_size)}) — using direct rclone upload")
        start = time.monotonic()
        success = upload_direct_rclone(filepath, f"{destination}/{Path(filepath).name}", config)
        elapsed = time.monotonic() - start
        if success:
            speed = file_size / elapsed if elapsed > 0 else 0
            logger.info(f"✓ Uploaded in {format_duration(elapsed)} ({format_size(int(speed))}/s)")
        else:
            logger.error("✗ Direct upload failed")
        return success

    # ─── Calculate chunks ─────────────────────────────────────────────────────
    total_chunks = math.ceil(file_size / config.chunk_size)

    print(f"\n{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.CYAN}  ⚡ cloud-boost v2 — Production Upload Engine")
    print(f"{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.WHITE}  File       : {Path(filepath).name}")
    print(f"{Fore.WHITE}  Size       : {format_size(file_size)}")
    print(f"{Fore.WHITE}  Target     : {destination}")
    print(f"{Fore.WHITE}  Chunks     : {total_chunks} × {format_size(config.chunk_size)}")
    print(f"{Fore.WHITE}  Threads    : {config.threads}")
    print(f"{Fore.WHITE}  Compress   : level {config.compression_level}")
    print(f"{Fore.WHITE}  Integrity  : SHA-256 {'ON' if config.checksum_verify else 'OFF'}")
    print(f"{Fore.WHITE}  Resume     : {'ON' if config.resume else 'OFF'}")
    if config.max_bandwidth > 0:
        print(f"{Fore.WHITE}  Bandwidth  : {format_size(config.max_bandwidth)}/s cap")
    print(f"{Fore.CYAN}{'═' * 60}\n")

    # ─── Load manifest (resume support) ───────────────────────────────────────
    manifest = load_manifest(filepath)

    if manifest["source_hash"] and config.resume:
        if file_changed(filepath, manifest):
            logger.warning("Source file changed since last upload — restarting from scratch")
            manifest = load_manifest.__wrapped__(filepath) if hasattr(load_manifest, '__wrapped__') else {
                "source_file": filepath,
                "source_hash": "",
                "total_chunks": 0,
                "chunk_size": 0,
                "chunks": {},
                "completed": False,
            }

    # Compute source hash for first time or after change
    if not manifest["source_hash"] and config.checksum_verify:
        logger.info("Computing source file hash (SHA-256)...")
        manifest["source_hash"] = compute_sha256(filepath)
        manifest["total_chunks"] = total_chunks
        manifest["chunk_size"] = config.chunk_size
        save_manifest(filepath, manifest)
        logger.info(f"Hash: {manifest['source_hash'][:16]}...")

    # ─── Count already-done chunks ────────────────────────────────────────────
    already_done = sum(
        1 for k, v in manifest["chunks"].items() if v.get("uploaded", False)
    )
    if already_done > 0:
        print(f"{Fore.MAGENTA}→ Resuming — {already_done}/{total_chunks} chunks already uploaded\n")

    # ─── Build chunk tasks ────────────────────────────────────────────────────
    chunk_tasks = []
    for i in range(total_chunks):
        offset = i * config.chunk_size
        actual_size = min(config.chunk_size, file_size - offset)
        chunk_tasks.append((i, offset, actual_size))

    # ─── Parallel upload ──────────────────────────────────────────────────────
    total_uploaded = 0
    total_skipped = 0
    total_failed = 0
    total_compressed_bytes = 0
    upload_start = time.monotonic()

    bar = tqdm(
        total=total_chunks,
        initial=0,
        desc=f"  {Fore.YELLOW}Uploading",
        unit="chunk",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    with ThreadPoolExecutor(max_workers=config.threads) as executor:
        futures = {
            executor.submit(
                _process_and_upload_chunk,
                filepath, idx, offset, size, destination, config, manifest,
            ): idx
            for idx, offset, size in chunk_tasks
        }

        for future in as_completed(futures):
            result = future.result()

            if result["success"]:
                if result["skipped"]:
                    total_skipped += 1
                else:
                    total_uploaded += 1
                    total_compressed_bytes += result["compressed_size"]
            else:
                total_failed += 1
                logger.error(
                    f"Chunk {result['chunk_index']} failed"
                    + (f": {result.get('error', 'unknown')}" if result.get("error") else "")
                )

            bar.update(1)

    bar.close()
    total_elapsed = time.monotonic() - upload_start

    # ─── Upload reassembly manifest ───────────────────────────────────────────
    reassembly = {
        "original_file": Path(filepath).name,
        "original_size": file_size,
        "original_hash": manifest.get("source_hash", ""),
        "total_chunks": total_chunks,
        "chunk_size": config.chunk_size,
        "compression": "gzip",
        "compression_level": config.compression_level,
    }
    upload_reassembly_manifest(destination, reassembly, config)

    # ─── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.CYAN}  Upload Summary")
    print(f"{Fore.CYAN}{'═' * 60}")

    if total_failed == 0:
        avg_speed = file_size / total_elapsed if total_elapsed > 0 else 0
        compression_ratio = (
            (1 - total_compressed_bytes / file_size) * 100
            if total_compressed_bytes > 0 and file_size > 0
            else 0
        )

        print(f"{Fore.GREEN}  Status     : ✓ COMPLETE")
        print(f"{Fore.WHITE}  Uploaded   : {total_uploaded} chunks")
        if total_skipped > 0:
            print(f"{Fore.MAGENTA}  Skipped    : {total_skipped} (already uploaded)")
        print(f"{Fore.WHITE}  Time       : {format_duration(total_elapsed)}")
        print(f"{Fore.WHITE}  Avg Speed  : {format_size(int(avg_speed))}/s")
        if compression_ratio > 0:
            print(f"{Fore.WHITE}  Compressed : {compression_ratio:.1f}% smaller")
        print(f"{Fore.CYAN}{'═' * 60}\n")

        # Mark complete and optionally clear manifest
        manifest["completed"] = True
        save_manifest(filepath, manifest)
        return True

    else:
        print(f"{Fore.RED}  Status     : ✗ INCOMPLETE")
        print(f"{Fore.WHITE}  Uploaded   : {total_uploaded}/{total_chunks}")
        print(f"{Fore.RED}  Failed     : {total_failed}")
        print(f"{Fore.YELLOW}  → Run 'cloudboost resume' to retry failed chunks")
        print(f"{Fore.CYAN}{'═' * 60}\n")
        return False


def upload_folder(
    folderpath: str,
    destination: str,
    config: UploadConfig | None = None,
) -> bool:
    """
    Upload all files in a folder recursively.
    Each file gets its own manifest for independent resume.
    """
    logger = get_logger("cloudboost")
    folder = Path(folderpath)

    if not folder.exists() or not folder.is_dir():
        logger.error(f"Folder not found: {folderpath}")
        return False

    # Collect all files
    files = sorted([f for f in folder.rglob("*") if f.is_file()])

    if not files:
        logger.error("Folder is empty — nothing to upload")
        return False

    total_size = sum(f.stat().st_size for f in files)
    logger.info(f"Found {len(files)} files ({format_size(total_size)}) in {folderpath}")

    success_count = 0
    fail_count = 0

    for i, file in enumerate(files, 1):
        rel_path = file.relative_to(folder)
        file_dest = f"{destination}/{rel_path.parent}" if str(rel_path.parent) != "." else destination

        print(f"\n{Fore.WHITE}[{i}/{len(files)}] {rel_path}")

        if upload_file(str(file), file_dest, config):
            success_count += 1
        else:
            fail_count += 1

    # Final summary
    print(f"\n{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.CYAN}  Folder Upload Summary")
    print(f"{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.GREEN}  Success : {success_count}/{len(files)}")
    if fail_count > 0:
        print(f"{Fore.RED}  Failed  : {fail_count}")
    print(f"{Fore.CYAN}{'═' * 60}\n")

    return fail_count == 0
