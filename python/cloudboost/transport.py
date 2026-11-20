"""
cloud-boost v3 — transport layer (REWRITTEN)
Single rclone invocation per file/batch. No per-chunk subprocess spawning.
Handles: optimized rclone flags, quota tracking, rate limit detection.
"""

import os
import subprocess
import shutil
import time
import json
from pathlib import Path
from dataclasses import dataclass, field

from cloudboost.config import UploadConfig, format_size, PROVIDERS
from cloudboost.logger import get_logger

logger = get_logger("cloudboost.transport")


# ─── Quota Tracker ────────────────────────────────────────────────────────────
@dataclass
class QuotaTracker:
    """Track cumulative upload bytes to respect Google Drive 750GB/day limit."""
    provider: str = "gdrive"
    daily_limit: int = 750 * 1024 * 1024 * 1024  # 750 GB
    session_bytes: int = 0
    warn_threshold: float = 0.93  # Warn at 93% (697 GB)

    def add(self, bytes_uploaded: int) -> None:
        self.session_bytes += bytes_uploaded

    def check(self) -> str:
        """Returns: 'ok', 'warning', or 'blocked'."""
        if self.provider != "gdrive":
            return "ok"
        if self.session_bytes >= self.daily_limit:
            return "blocked"
        if self.session_bytes >= int(self.daily_limit * self.warn_threshold):
            return "warning"
        return "ok"

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.session_bytes)

    def usage_pct(self) -> float:
        return (self.session_bytes / self.daily_limit) * 100 if self.daily_limit else 0


# ─── rclone checks ───────────────────────────────────────────────────────────
def check_rclone_installed() -> bool:
    """Verify rclone is installed and accessible."""
    return shutil.which("rclone") is not None


def get_rclone_version() -> str:
    """Get rclone version string."""
    try:
        result = subprocess.run(
            ["rclone", "version", "--check"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().split("\n")[0] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def get_rclone_remotes() -> list[str]:
    """List configured rclone remotes."""
    try:
        result = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
    except Exception:
        pass
    return []


def check_remote_accessible(remote: str) -> bool:
    """Quick check if a remote is accessible (token not expired, etc)."""
    try:
        result = subprocess.run(
            ["rclone", "about", remote, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


# ─── Build optimized rclone command ──────────────────────────────────────────
def build_rclone_flags(config: UploadConfig) -> list[str]:
    """
    Build optimized rclone flags based on config and provider.
    These flags are appended to any rclone command.
    """
    flags = [
        f"--transfers={config.rclone_transfers}",
        f"--buffer-size={config.rclone_buffer_size}",
        "--checkers=16",
        "--contimeout=60s",
        "--timeout=600s",
        "--retries=5",
        "--retries-sleep=3s",
        "--low-level-retries=10",
        "--stats=1s",
        "--stats-one-line",
        f"--tpslimit={config.tps_limit}",
        "--fast-list",
    ]

    # Provider-specific flags
    if config.provider == "gdrive":
        flags.extend([
            f"--drive-chunk-size={config.rclone_drive_chunk}",
            "--drive-upload-cutoff=8M",
            "--drive-use-trash=false",
            "--drive-pacer-min-sleep=10ms",
            "--drive-pacer-burst=200",
        ])
    elif config.provider == "s3":
        flags.extend([
            "--s3-upload-concurrency=8",
            "--s3-chunk-size=64M",
        ])

    # Bandwidth limiting
    if config.max_bandwidth > 0:
        bw = format_size(config.max_bandwidth).replace(" ", "")
        flags.append(f"--bwlimit={bw}")

    # Max transfer limit (quota protection)
    if config.max_transfer > 0:
        mt = format_size(config.max_transfer).replace(" ", "")
        flags.append(f"--max-transfer={mt}")

    # JSON log output for structured parsing
    if config.verbose:
        flags.append("--verbose")

    return flags


# ─── Upload functions ────────────────────────────────────────────────────────
def upload_file_rclone(
    source: str,
    destination: str,
    config: UploadConfig,
    quota: QuotaTracker | None = None,
) -> tuple[bool, str]:
    """
    Upload a single file using ONE rclone call with optimized flags.
    Returns (success, error_message).
    """
    # Quota pre-check
    if quota:
        status = quota.check()
        if status == "blocked":
            return (False, "Daily upload quota (750GB) reached. Try again tomorrow.")
        if status == "warning":
            remaining = format_size(quota.remaining())
            logger.warning(f"Approaching daily quota — {remaining} remaining")

    file_size = os.path.getsize(source)
    dest_path = f"{destination}/{Path(source).name}"

    cmd = ["rclone", "copyto", source, dest_path] + build_rclone_flags(config)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(3600, file_size // (1024 * 1024) * 10),  # ~10s per MB minimum
        )

        if result.returncode == 0:
            if quota:
                quota.add(file_size)
            return (True, "")

        # Parse error
        stderr = result.stderr.lower()
        if "rate limit" in stderr or "403" in stderr or "rateLimitExceeded" in stderr:
            return (False, "Google Drive rate limit hit. Backing off...")
        if "quota" in stderr:
            return (False, "Google Drive quota exceeded (750GB/day).")
        return (False, result.stderr.strip()[:200])

    except subprocess.TimeoutExpired:
        return (False, "Upload timed out")
    except Exception as e:
        return (False, f"Unexpected error: {type(e).__name__}: {str(e)[:100]}")


def upload_folder_rclone(
    source_dir: str,
    destination: str,
    config: UploadConfig,
    quota: QuotaTracker | None = None,
) -> tuple[bool, str]:
    """
    Upload an entire folder using ONE rclone call.
    rclone handles its own parallelism via --transfers.
    """
    if quota:
        status = quota.check()
        if status == "blocked":
            return (False, "Daily upload quota (750GB) reached.")

    cmd = ["rclone", "copy", source_dir, destination] + build_rclone_flags(config)

    try:
        # For folders, stream output in real-time
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        _, stderr = process.communicate(timeout=86400)  # 24h max

        if process.returncode == 0:
            return (True, "")

        return (False, stderr.strip()[:200] if stderr else "Unknown error")

    except subprocess.TimeoutExpired:
        process.kill()
        return (False, "Upload timed out (24h limit)")
    except Exception as e:
        return (False, f"Unexpected error: {type(e).__name__}")


def upload_with_progress(
    source: str,
    destination: str,
    config: UploadConfig,
    quota: QuotaTracker | None = None,
    progress_callback=None,
) -> tuple[bool, str]:
    """
    Upload with real-time progress tracking via rclone --stats output.
    Streams stderr for progress updates.
    """
    is_dir = os.path.isdir(source)
    rclone_cmd = "copy" if is_dir else "copyto"
    dest = destination if is_dir else f"{destination}/{Path(source).name}"

    cmd = ["rclone", rclone_cmd, source, dest] + build_rclone_flags(config)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Read progress from stderr line by line, keep last N for error capture
        last_lines: list[str] = []
        for line in iter(process.stderr.readline, ""):
            line = line.strip()
            if line:
                last_lines.append(line)
                if len(last_lines) > 20:
                    last_lines.pop(0)
                if progress_callback:
                    progress_callback(line)

        process.wait(timeout=86400)

        if process.returncode == 0:
            if quota:
                total_size = (
                    sum(f.stat().st_size for f in Path(source).rglob("*") if f.is_file())
                    if is_dir else os.path.getsize(source)
                )
                quota.add(total_size)
            return (True, "")

        # Extract actual rclone error from captured stderr tail
        error_detail = "rclone exited with non-zero status"
        if last_lines:
            # Filter out stats lines, find actual error messages
            error_lines = [l for l in last_lines if "ERROR" in l or "Failed" in l or "fatal" in l.lower()]
            if error_lines:
                error_detail = error_lines[-1][:300]
            else:
                error_detail = last_lines[-1][:300]
        return (False, error_detail)

    except subprocess.TimeoutExpired:
        process.kill()
        return (False, "Upload timed out")
    except Exception as e:
        return (False, str(e)[:200])



# ─── Server-side copy detection ──────────────────────────────────────────────

def detect_rclone_mount(source_path: str) -> tuple[str, str] | None:
    """
    Detect if source_path resides on an rclone-mounted drive (Windows).

    Inspects running rclone mount processes to find which drive letter
    maps to which remote. Returns (remote_name, remote_relative_path)
    if the source is on a mounted remote, None otherwise.

    Example:
        Mount:  rclone mount gdrive: Z: --vfs-cache-mode full
        Source: Z:\\Movie\\file.mkv
        Return: ("gdrive:", "Movie/file.mkv")
    """
    source_path = os.path.abspath(source_path)

    # Windows drive letter check
    if len(source_path) < 3 or source_path[1] != ':':
        return None

    drive_letter = source_path[0].upper()

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='rclone.exe'\").CommandLine"],
            capture_output=True, text=True, timeout=10,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        for cmdline in result.stdout.strip().split('\n'):
            cmdline = cmdline.strip()
            if not cmdline:
                continue

            # Find " mount " as substring (avoids quoted-path split issues)
            mount_pos = cmdline.lower().find(' mount ')
            if mount_pos == -1:
                continue

            # Parse args after "mount ": remote, mount_point, ...flags
            after_mount = cmdline[mount_pos + 7:].strip()
            after_parts = after_mount.split()
            if len(after_parts) < 2:
                continue

            remote_arg = after_parts[0]    # e.g. "gdrive:" or "gdrive:subfolder"
            mount_point = after_parts[1]   # e.g. "Z:" or "Z:\"

            # Extract drive letter from mount point
            mount_drive = mount_point.rstrip(':\\/').upper()

            if len(mount_drive) != 1 or mount_drive != drive_letter:
                continue

            # Validate remote_arg has ':'
            if ':' not in remote_arg:
                continue

            colon_pos = remote_arg.index(':')
            remote_name = remote_arg[:colon_pos + 1]   # "gdrive:"
            base_path = remote_arg[colon_pos + 1:]      # "" or "subfolder"

            # Build full remote path from drive-relative path
            relative = source_path[3:]  # Strip "Z:\"
            relative = relative.replace('\\', '/')

            if base_path:
                full_path = f"{base_path.strip('/')}/{relative}"
            else:
                full_path = relative

            return (remote_name, full_path)

    except Exception:
        pass

    return None


def server_side_copy(
    source_remote: str,
    source_path: str,
    destination: str,
    progress_callback=None,
) -> tuple[bool, str]:
    """
    Perform a server-side copy on the same rclone remote.
    No data flows through the local machine — Google Drive copies internally.

    Args:
        source_remote: Remote name, e.g. "gdrive:"
        source_path:   Path on remote, e.g. "Movie/file.mkv"
        destination:   Full rclone destination, e.g. "gdrive:/Backup/"
    """
    source_full = f"{source_remote}{source_path}"

    # Build destination path with filename
    filename = source_path.rsplit('/', 1)[-1] if '/' in source_path else source_path
    dest_full = f"{destination.rstrip('/')}/{filename}"

    cmd = [
        "rclone", "copyto",
        source_full, dest_full,
        "--drive-server-side-across-configs",
        "--contimeout=60s",
        "--timeout=600s",
        "--retries=5",
        "--retries-sleep=3s",
        "--low-level-retries=10",
        "--stats=1s",
        "--stats-one-line",
        "--verbose",
    ]

    logger.info(f"Server-side copy: {source_full} → {dest_full}")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        last_lines: list[str] = []
        for line in iter(process.stderr.readline, ""):
            line = line.strip()
            if line:
                last_lines.append(line)
                if len(last_lines) > 20:
                    last_lines.pop(0)
                if progress_callback:
                    progress_callback(line)

        process.wait(timeout=600)

        if process.returncode == 0:
            return (True, "")

        error_detail = "Server-side copy failed"
        if last_lines:
            error_lines = [l for l in last_lines
                           if "ERROR" in l or "Failed" in l or "fatal" in l.lower()]
            if error_lines:
                error_detail = error_lines[-1][:300]
            else:
                error_detail = last_lines[-1][:300]
        return (False, error_detail)

    except subprocess.TimeoutExpired:
        process.kill()
        return (False, "Server-side copy timed out")
    except Exception as e:
        return (False, str(e)[:200])



def normalize_destination(destination: str, default_remote: str = "gdrive:") -> str:
    """
    Clean and resolve destination string to a valid rclone remote path:
    - Strips surrounding quotes (single and double)
    - If path points to an rclone mounted drive (e.g. Z:\\Movie or "Z:\\Movie"),
      resolves it to remote path (e.g. gdrive:/Movie)
    - If path has no remote prefix (e.g. 'Movie' or 'Backup' or '/Backup/'),
      prepends default_remote
    - Normalizes slashes to forward slashes
    """
    if not destination:
        return f"{default_remote.rstrip('/')}/Backup/"

    dest = destination.strip().strip('"').strip("'")
    if not dest:
        return f"{default_remote.rstrip('/')}/Backup/"

    # Check if dest is on an rclone mount (e.g. Z:\\Movie or Z:/Movie)
    mount_info = detect_rclone_mount(dest)
    if mount_info:
        remote, remote_path = mount_info
        clean_path = remote_path.lstrip('/')
        return f"{remote}/{clean_path}" if clean_path else remote

    # Check if Windows drive format (e.g. C:\\ or Z:\\)
    is_windows_drive = len(dest) >= 2 and dest[1] == ':' and (len(dest) == 2 or dest[2] in ('\\', '/'))
    if is_windows_drive:
        drive_path = dest[2:].lstrip('\\/').replace('\\', '/')
        return f"{default_remote.rstrip('/')}/{drive_path}"

    if ':' not in dest:
        clean_path = dest.strip('/\\').replace('\\', '/')
        return f"{default_remote.rstrip('/')}/{clean_path}"

    remote_part, path_part = dest.split(':', 1)
    clean_path = path_part.replace('\\', '/')
    return f"{remote_part}:{clean_path}"
