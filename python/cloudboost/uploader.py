"""
cloud-boost v3 — core uploader (REWRITTEN)
Two-tier upload strategy with smart compression and single-rclone-call architecture.

Strategy:
  < 50MB:   Direct rclone upload (no processing)
  50MB+:    Entropy analysis → optional compression → rclone upload with optimized flags
  Folder:   Pre-process compressible files → single rclone copy with --transfers
  250GB+:   Same as above, rclone handles its own chunking natively
"""

import os
import re
import sys
import time
import shutil
import tempfile
from pathlib import Path
from colorama import Fore, Style, init

from cloudboost.config import UploadConfig, format_size, format_duration, MB, GB, KB, ensure_dirs
from cloudboost.entropy import analyze_file, CompressionDecision
from cloudboost.compressor import compress_file, get_compressor_name, get_compression_ext
from cloudboost.transport import (
    check_rclone_installed,
    get_rclone_remotes,
    upload_file_rclone,
    upload_folder_rclone,
    upload_with_progress,
    detect_rclone_mount,
    server_side_copy,
    QuotaTracker,
)
from cloudboost.manifest import (
    create_manifest,
    load_manifest,
    save_manifest,
    mark_uploading,
    mark_completed,
    mark_failed,
    mark_compressed,
    is_already_uploaded,
)
from cloudboost.logger import get_logger

init(autoreset=True)

SEPARATOR = "═" * 60

# Terminal capability detection for progress bar glyphs
try:
    "█░⠋".encode(sys.stdout.encoding or "utf-8")
    BAR_FILLED = "█"
    BAR_EMPTY = "░"
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
except Exception:
    BAR_FILLED = "#"
    BAR_EMPTY = "-"
    SPINNER_FRAMES = ["|", "/", "-", "\\"]


class AnimatedProgressBar:
    """
    Animated terminal progress bar with spinning glyph, percentage,
    transfer stats, speed, and ETA parsed dynamically from rclone output.
    """

    def __init__(self, total_size: int = 0, bar_length: int = 26):
        self.total_size = total_size
        self.bar_length = bar_length
        self.percent = 0.0
        self.speed = "--"
        self.eta = "--"
        self.xfer_str = ""
        self.frame_idx = 0
        self.rendered = False

    def start(self, label: str = "Connecting...") -> None:
        self.rendered = True
        self.render(label=label)

    def update_from_rclone(self, line: str) -> None:
        if not line:
            return

        # Check percentage: e.g. "15%" or "15.5%"
        pct_match = re.search(r"(\d+(?:\.\d+)?)%", line)
        if pct_match:
            try:
                self.percent = min(100.0, float(pct_match.group(1)))
            except ValueError:
                pass

        # Check speed: e.g. "890.120 KiB/s" or "1.25 MiB/s"
        speed_match = re.search(r"([\d.]+\s*[KMGTPE]?i?B/s)", line)
        if speed_match:
            self.speed = speed_match.group(1).strip()

        # Check ETA: e.g. "ETA 3m25s" or ", 3m25s"
        eta_match = re.search(r"ETA\s+([0-9a-zA-Z]+)", line)
        if eta_match:
            self.eta = eta_match.group(1).strip()
        else:
            tail_eta = re.search(r",\s*(\d+[smhd](?:\d+[smhd])*)", line)
            if tail_eta:
                self.eta = tail_eta.group(1).strip()

        # Check transferred / total: e.g. "32.450 MiB / 215.840 MiB"
        xfer_match = re.search(r"([\d.]+\s*[KMGTPE]?i?B)\s*/\s*([\d.]+\s*[KMGTPE]?i?B)", line)
        if xfer_match:
            self.xfer_str = f"{xfer_match.group(1).strip()}/{xfer_match.group(2).strip()}"
        elif self.total_size > 0 and self.percent > 0:
            cur = int(self.total_size * (self.percent / 100.0))
            self.xfer_str = f"{format_size(cur)}/{format_size(self.total_size)}"

        self.render()

    def render(self, label: str = "") -> None:
        self.frame_idx = (self.frame_idx + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self.frame_idx]

        filled_len = int(self.bar_length * (self.percent / 100.0))
        filled_len = max(0, min(self.bar_length, filled_len))
        empty_len = self.bar_length - filled_len

        bar = f"{Fore.GREEN}{BAR_FILLED * filled_len}{Fore.LIGHTBLACK_EX}{BAR_EMPTY * empty_len}{Style.RESET_ALL}"

        parts = [
            f"\r  {Fore.CYAN}{spinner}{Style.RESET_ALL} [{bar}] {Fore.YELLOW}{self.percent:5.1f}%{Style.RESET_ALL}"
        ]

        if label:
            parts.append(f"{Fore.CYAN}{label}{Style.RESET_ALL}")
        else:
            if self.xfer_str:
                parts.append(f"{Fore.CYAN}{self.xfer_str}{Style.RESET_ALL}")
            if self.speed != "--":
                parts.append(f"{Fore.GREEN}{self.speed}{Style.RESET_ALL}")
            if self.eta != "--":
                parts.append(f"ETA: {Fore.MAGENTA}{self.eta}{Style.RESET_ALL}")

        status_text = " | ".join(parts)
        sys.stdout.write(f"{status_text:<115}")
        sys.stdout.flush()

    def complete(self) -> None:
        bar = f"{Fore.GREEN}{BAR_FILLED * self.bar_length}{Style.RESET_ALL}"
        total_str = format_size(self.total_size) if self.total_size > 0 else ""
        tail = f" | {Fore.CYAN}{total_str}{Style.RESET_ALL}" if total_str else ""
        sys.stdout.write(f"\r  {Fore.GREEN}✓{Style.RESET_ALL} [{bar}] {Fore.GREEN}100.0%{Style.RESET_ALL}{tail} | {Fore.GREEN}Done!{Style.RESET_ALL}                              \n")
        sys.stdout.flush()

    def fail(self) -> None:
        filled_len = int(self.bar_length * (self.percent / 100.0))
        filled_len = max(0, min(self.bar_length, filled_len))
        empty_len = self.bar_length - filled_len
        bar = f"{Fore.RED}{BAR_FILLED * filled_len}{Fore.LIGHTBLACK_EX}{BAR_EMPTY * empty_len}{Style.RESET_ALL}"
        sys.stdout.write(f"\r  {Fore.RED}✗{Style.RESET_ALL} [{bar}] {Fore.RED}{self.percent:5.1f}%{Style.RESET_ALL} | {Fore.RED}Interrupted{Style.RESET_ALL}                          \n")
        sys.stdout.flush()


def _print_header(filepath: str, destination: str, config: UploadConfig, file_size: int) -> None:
    """Print the upload banner with file info and config."""
    print(f"\n{Fore.CYAN}{SEPARATOR}")
    print(f"{Fore.CYAN}  ⚡ cloud-boost v3 — Production Upload Engine")
    print(f"{Fore.CYAN}{SEPARATOR}")
    print(f"  File       : {Path(filepath).name}")
    print(f"  Size       : {format_size(file_size)}")
    print(f"  Target     : {destination}")
    print(f"  Provider   : {config.provider}")
    print(f"  Compressor : {get_compressor_name()}")
    print(f"  rclone     : transfers={config.rclone_transfers}, "
          f"chunk={config.rclone_drive_chunk}, "
          f"tps={config.tps_limit}")
    if config.max_bandwidth > 0:
        print(f"  Bandwidth  : {format_size(config.max_bandwidth)}/s cap")
    print(f"{Fore.CYAN}{SEPARATOR}\n")


def _print_summary(
    success: bool,
    filepath: str,
    file_size: int,
    elapsed: float,
    was_compressed: bool = False,
    compressed_size: int = 0,
    error: str = "",
) -> None:
    """Print upload result summary."""
    print(f"\n{Fore.CYAN}{SEPARATOR}")
    print(f"{Fore.CYAN}  Upload Summary")
    print(f"{Fore.CYAN}{SEPARATOR}")

    if success:
        speed = file_size / elapsed if elapsed > 0 else 0
        print(f"{Fore.GREEN}  Status     : ✓ COMPLETE")
        print(f"  File       : {Path(filepath).name}")
        print(f"  Size       : {format_size(file_size)}")
        if was_compressed and compressed_size > 0:
            ratio = (1 - compressed_size / file_size) * 100
            print(f"  Compressed : {format_size(compressed_size)} ({ratio:.1f}% smaller)")
        print(f"  Time       : {format_duration(elapsed)}")
        print(f"  Avg Speed  : {format_size(int(speed))}/s")
    else:
        print(f"{Fore.RED}  Status     : ✗ FAILED")
        print(f"  File       : {Path(filepath).name}")
        if error:
            print(f"{Fore.RED}  Error      : {error}")
        print(f"{Fore.YELLOW}  → Run 'cloudboost resume' to retry")

    print(f"{Fore.CYAN}{SEPARATOR}\n")


def upload_file(
    filepath: str,
    destination: str,
    config: UploadConfig | None = None,
    quota: QuotaTracker | None = None,
) -> bool:
    """
    Upload a single file with smart compression and optimized rclone.

    Pipeline:
    1. Validate inputs
    2. Check if already uploaded (dedup)
    3. Analyze entropy → decide compression
    4. Compress if beneficial (zstd/gzip)
    5. Upload via rclone (single call, optimized flags)
    6. Track quota + save manifest
    """
    logger = get_logger("cloudboost", verbose=config.verbose if config else False)
    ensure_dirs()

    # ─── Validate ─────────────────────────────────────────────────────────────
    filepath = os.path.abspath(filepath)

    if not Path(filepath).exists():
        logger.error(f"File not found: {filepath}")
        return False

    if not Path(filepath).is_file():
        logger.error(f"Not a file: {filepath} — use upload_folder for directories")
        return False

    if not check_rclone_installed():
        logger.error("rclone not installed. Get it from https://rclone.org/install/")
        return False

    file_size = os.path.getsize(filepath)
    if file_size == 0:
        logger.error("File is empty — nothing to upload")
        return False

    # ─── Auto-tune config ─────────────────────────────────────────────────────
    if config is None:
        config = UploadConfig.auto_tune(file_size)

    # ─── Dedup check ──────────────────────────────────────────────────────────
    if config.dedup and is_already_uploaded(filepath, destination):
        logger.info(f"Already uploaded: {Path(filepath).name} → skipping")
        return True

    # ─── Server-side copy detection ──────────────────────────────────────────
    mount_info = detect_rclone_mount(filepath)
    if mount_info:
        source_remote, remote_path = mount_info
        dest_remote = destination[:destination.index(':') + 1] if ':' in destination else ''

        if dest_remote and source_remote.lower() == dest_remote.lower():
            # Same remote — instant server-side copy, zero local bandwidth
            print(f"\n{Fore.CYAN}{SEPARATOR}")
            print(f"{Fore.GREEN}  ⚡ SERVER-SIDE COPY — Zero Local Bandwidth!")
            print(f"{Fore.CYAN}{SEPARATOR}")
            print(f"  File       : {Path(filepath).name}")
            print(f"  Size       : {format_size(file_size)}")
            print(f"  Source     : {source_remote}{remote_path}")
            print(f"  Target     : {destination}")
            print(f"{Fore.GREEN}  Mode       : Drive server-side copy (instant)")
            print(f"{Fore.CYAN}{SEPARATOR}\n")

            manifest = create_manifest(filepath)
            mark_uploading(filepath, manifest, destination)

            start = time.monotonic()

            progress_bar = AnimatedProgressBar(total_size=file_size)
            progress_bar.start("Server-side copy in progress...")

            success, error = server_side_copy(
                source_remote, remote_path, destination,
                progress_callback=lambda line: progress_bar.update_from_rclone(line)
            )

            elapsed = time.monotonic() - start

            if success:
                progress_bar.complete()
                mark_completed(filepath, manifest)
            else:
                progress_bar.fail()
                mark_failed(filepath, manifest, error)

            _print_summary(success, filepath, file_size, elapsed, error=error)
            return success

    # ─── Print header ─────────────────────────────────────────────────────────
    _print_header(filepath, destination, config, file_size)

    # ─── Create manifest ──────────────────────────────────────────────────────
    manifest = create_manifest(filepath)
    mark_uploading(filepath, manifest, destination)

    # ─── Quota check ──────────────────────────────────────────────────────────
    if quota is None:
        quota = QuotaTracker(provider=config.provider)

    quota_status = quota.check()
    if quota_status == "blocked":
        logger.error("Daily upload quota (750GB) reached. Try again tomorrow.")
        mark_failed(filepath, manifest, "Quota exceeded")
        return False
    if quota_status == "warning":
        logger.warning(f"⚠ Approaching daily quota — {format_size(quota.remaining())} left")

    # ─── Small file fast path (< 50MB) ────────────────────────────────────────
    if file_size < 50 * MB:
        logger.info(f"Small file ({format_size(file_size)}) → direct upload")
        start = time.monotonic()
        success, error = upload_file_rclone(filepath, destination, config, quota)
        elapsed = time.monotonic() - start

        if success:
            mark_completed(filepath, manifest)
        else:
            mark_failed(filepath, manifest, error)

        _print_summary(success, filepath, file_size, elapsed, error=error)
        return success

    # ─── Entropy analysis + compression ───────────────────────────────────────
    logger.info("Analyzing file entropy...")
    decision = analyze_file(filepath)
    logger.info(f"  → {decision}")

    upload_path = filepath  # what we'll actually upload
    was_compressed = False
    compressed_size = 0

    if decision.should_compress:
        logger.info(f"Compressing with {get_compressor_name()} (level {decision.level})...")
        compress_start = time.monotonic()

        # Create temp compressed file
        temp_dir = tempfile.mkdtemp(prefix="cloudboost_")
        compressed_path = os.path.join(
            temp_dir, Path(filepath).name + get_compression_ext()
        )

        output_path, output_size, actually_compressed = compress_file(
            filepath, compressed_path, decision
        )

        compress_elapsed = time.monotonic() - compress_start

        if actually_compressed:
            was_compressed = True
            compressed_size = output_size
            upload_path = output_path
            ratio = (1 - output_size / file_size) * 100
            logger.info(
                f"  → Compressed: {format_size(output_size)} "
                f"({ratio:.1f}% smaller, took {format_duration(compress_elapsed)})"
            )
            mark_compressed(
                filepath, manifest, compressed_path, output_size, get_compressor_name()
            )
        else:
            logger.info(f"  → Compression skipped (no benefit)")
            # Clean up temp dir
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        logger.info(f"  → Compression skipped: {decision.reason}")

    # ─── Upload via rclone ────────────────────────────────────────────────────
    actual_upload_size = os.path.getsize(upload_path)
    logger.info(
        f"Uploading {format_size(actual_upload_size)} "
        f"via rclone ({config.rclone_transfers} transfers, "
        f"chunk={config.rclone_drive_chunk})..."
    )

    upload_start = time.monotonic()

    progress_bar = AnimatedProgressBar(total_size=actual_upload_size)
    progress_bar.start("Connecting to remote...")

    def progress_cb(line: str) -> None:
        """Parse rclone progress into animated progress bar."""
        progress_bar.update_from_rclone(line)

    success, error = upload_with_progress(upload_path, destination, config, quota, progress_cb)
    if success:
        progress_bar.complete()
    else:
        progress_bar.fail()

    upload_elapsed = time.monotonic() - upload_start
    total_elapsed = upload_elapsed  # could add compress time too

    # ─── Cleanup compressed temp file ─────────────────────────────────────────
    if was_compressed and upload_path != filepath:
        temp_dir = str(Path(upload_path).parent)
        shutil.rmtree(temp_dir, ignore_errors=True)

    # ─── Record result ────────────────────────────────────────────────────────
    if success:
        mark_completed(filepath, manifest)
    else:
        mark_failed(filepath, manifest, error)

    _print_summary(
        success, filepath, file_size, total_elapsed,
        was_compressed, compressed_size, error,
    )
    return success


def upload_folder(
    folderpath: str,
    destination: str,
    config: UploadConfig | None = None,
    quota: QuotaTracker | None = None,
) -> bool:
    """
    Upload a folder with smart pre-processing.

    Strategy:
    1. Scan all files
    2. Separate into compressible vs already-compressed
    3. For compressible files: compress into staging dir
    4. Upload everything via single rclone copy call
    """
    logger = get_logger("cloudboost", verbose=config.verbose if config else False)
    ensure_dirs()

    folder = Path(folderpath).resolve()
    if not folder.exists() or not folder.is_dir():
        logger.error(f"Folder not found: {folderpath}")
        return False

    if not check_rclone_installed():
        logger.error("rclone not installed.")
        return False

    # Collect files
    files = sorted([f for f in folder.rglob("*") if f.is_file()])
    if not files:
        logger.error("Folder is empty")
        return False

    total_size = sum(f.stat().st_size for f in files)

    if config is None:
        config = UploadConfig.auto_tune(total_size)

    if quota is None:
        quota = QuotaTracker(provider=config.provider)

    print(f"\n{Fore.CYAN}{SEPARATOR}")
    print(f"{Fore.CYAN}  ⚡ cloud-boost v3 — Folder Upload")
    print(f"{Fore.CYAN}{SEPARATOR}")
    print(f"  Folder     : {folder}")
    print(f"  Files      : {len(files)}")
    print(f"  Total Size : {format_size(total_size)}")
    print(f"  Target     : {destination}")
    print(f"  Provider   : {config.provider}")
    print(f"{Fore.CYAN}{SEPARATOR}\n")

    # ─── Analyze all files ────────────────────────────────────────────────────
    logger.info("Analyzing files for compression...")
    compressible = []
    skip_compress = []

    for f in files:
        decision = analyze_file(str(f))
        if decision.should_compress and f.stat().st_size > 1 * MB:
            compressible.append((f, decision))
        else:
            skip_compress.append(f)

    logger.info(f"  → {len(compressible)} files to compress, {len(skip_compress)} already compressed/small")

    # ─── Create staging directory with compressed files ───────────────────────
    staging_dir = None
    if compressible:
        staging_dir = Path(tempfile.mkdtemp(prefix="cloudboost_stage_"))
        logger.info(f"Pre-compressing {len(compressible)} files...")

        compressed_count = 0
        saved_bytes = 0

        for f, decision in compressible:
            rel = f.relative_to(folder)
            staged_path = staging_dir / rel
            staged_path.parent.mkdir(parents=True, exist_ok=True)

            output_path, output_size, actually_compressed = compress_file(
                str(f), str(staged_path) + get_compression_ext(), decision
            )

            if actually_compressed:
                compressed_count += 1
                saved_bytes += f.stat().st_size - output_size
            else:
                # Copy original to staging
                shutil.copy2(str(f), str(staged_path))

        # Copy non-compressible files to staging
        for f in skip_compress:
            rel = f.relative_to(folder)
            staged_path = staging_dir / rel
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(staged_path))

        if compressed_count > 0:
            logger.info(f"  → Compressed {compressed_count} files, saved {format_size(saved_bytes)}")

        upload_source = str(staging_dir)
    else:
        # Nothing to compress — upload folder directly
        upload_source = str(folder)

    # ─── Upload via single rclone call ────────────────────────────────────────
    logger.info(f"Uploading via rclone ({config.rclone_transfers} parallel transfers)...")
    upload_start = time.monotonic()

    progress_bar = AnimatedProgressBar(total_size=total_size)
    progress_bar.start(f"{len(files)} files queued...")

    def folder_progress_cb(line: str) -> None:
        """Parse rclone progress into animated progress bar."""
        progress_bar.update_from_rclone(line)

    success, error = upload_with_progress(upload_source, destination, config, quota, folder_progress_cb)
    if success:
        progress_bar.complete()
    else:
        progress_bar.fail()

    elapsed = time.monotonic() - upload_start

    # ─── Cleanup staging ──────────────────────────────────────────────────────
    if staging_dir and staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    # ─── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}{SEPARATOR}")
    print(f"{Fore.CYAN}  Folder Upload Summary")
    print(f"{Fore.CYAN}{SEPARATOR}")

    if success:
        speed = total_size / elapsed if elapsed > 0 else 0
        print(f"{Fore.GREEN}  Status     : ✓ COMPLETE")
        print(f"  Files      : {len(files)}")
        print(f"  Total      : {format_size(total_size)}")
        print(f"  Time       : {format_duration(elapsed)}")
        print(f"  Avg Speed  : {format_size(int(speed))}/s")
        quota_pct = quota.usage_pct()
        if quota_pct > 0:
            print(f"  Quota Used : {quota_pct:.1f}% of 750GB daily limit")
    else:
        print(f"{Fore.RED}  Status     : ✗ FAILED")
        if error:
            print(f"{Fore.RED}  Error      : {error}")

    print(f"{Fore.CYAN}{SEPARATOR}\n")
    return success
