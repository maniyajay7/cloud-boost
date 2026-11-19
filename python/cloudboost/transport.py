"""
cloud-boost transport layer
Handles: rclone subprocess execution, retries, bandwidth throttling
"""

import subprocess
import time
import shutil
from pathlib import Path
from cloudboost.config import UploadConfig, format_size
from cloudboost.logger import get_logger

logger = get_logger("cloudboost.transport")


def check_rclone_installed() -> bool:
    """Verify rclone is installed and accessible."""
    return shutil.which("rclone") is not None


def get_rclone_remotes() -> list[str]:
    """List configured rclone remotes."""
    try:
        result = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def build_rclone_command(
    source: str,
    destination: str,
    config: UploadConfig,
) -> list[str]:
    """Build the rclone command with optimized flags."""
    cmd = [
        "rclone",
        "copyto",
        source,
        destination,
        f"--transfers={config.rclone_transfers}",
        f"--buffer-size={config.rclone_buffer_size}",
        "--checkers=16",
        "--contimeout=30s",
        "--timeout=300s",
        "--retries=3",
        "--low-level-retries=5",
        "--stats=0",
    ]

    # Provider-specific optimizations
    if config.provider == "gdrive":
        cmd.append(f"--drive-chunk-size={config.rclone_drive_chunk}")
        cmd.append("--drive-upload-cutoff=8M")
        cmd.append("--drive-use-trash=false")

    # Bandwidth limiting
    if config.max_bandwidth > 0:
        bw_str = format_size(config.max_bandwidth).replace(" ", "")
        cmd.append(f"--bwlimit={bw_str}")

    return cmd


def upload_chunk(
    chunk_path: str,
    destination: str,
    chunk_index: int,
    config: UploadConfig,
) -> bool:
    """
    Upload a single chunk via rclone with retry logic.
    Returns True on success, False on failure after all retries.
    """
    sub_dir = f"{chunk_index // 256:02x}"
    chunk_name = f"chunk_{chunk_index:06d}.gz"
    dest_path = f"{destination}/.cb_upload/{sub_dir}/{chunk_name}"

    for attempt in range(1, config.max_retries + 1):
        try:
            cmd = build_rclone_command(chunk_path, dest_path, config)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 min max per chunk
            )

            if result.returncode == 0:
                return True

            if attempt < config.max_retries:
                delay = config.retry_delay * (2 ** (attempt - 1))  # exponential backoff
                logger.warning(
                    f"Chunk {chunk_index} attempt {attempt}/{config.max_retries} failed, "
                    f"retrying in {delay:.0f}s"
                )
                time.sleep(delay)

        except subprocess.TimeoutExpired:
            logger.warning(f"Chunk {chunk_index} timed out on attempt {attempt}")
            if attempt < config.max_retries:
                time.sleep(config.retry_delay)
        except Exception as e:
            logger.error(f"Chunk {chunk_index} unexpected error: {type(e).__name__}")
            if attempt < config.max_retries:
                time.sleep(config.retry_delay)

    return False


def upload_direct_rclone(
    source: str,
    destination: str,
    config: UploadConfig,
) -> bool:
    """
    Upload a file directly via rclone (for small files that don't need chunking).
    Uses optimized rclone flags only.
    """
    cmd = build_rclone_command(source, destination, config)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.error(f"Direct upload failed: {type(e).__name__}")
        return False


def upload_reassembly_manifest(
    destination: str,
    manifest_data: dict,
    config: UploadConfig,
) -> bool:
    """Upload the reassembly manifest to the destination so chunks can be rebuilt."""
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(manifest_data, f, indent=2)
        temp_path = f.name

    dest_path = f"{destination}/.cb_upload/manifest.json"
    cmd = build_rclone_command(temp_path, dest_path, config)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    finally:
        Path(temp_path).unlink(missing_ok=True)
