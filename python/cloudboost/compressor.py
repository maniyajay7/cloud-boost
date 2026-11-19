"""
cloud-boost v3 — compressor module
Streaming compression with zstd (primary) and gzip (fallback).
Automatically selects algorithm and level based on entropy analysis.
"""

import io
import os
import tempfile
from pathlib import Path

# Try zstd first (5-10x faster than gzip), fall back to gzip
try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

import gzip

from cloudboost.entropy import analyze_file, CompressionDecision
from cloudboost.logger import get_logger

logger = get_logger("cloudboost.compressor")

# ─── Constants ────────────────────────────────────────────────────────────────
STREAM_BUFFER = 1024 * 1024  # 1MB read buffer for streaming compression


def compress_file_zstd(
    src_path: str,
    dst_path: str,
    level: int = 3,
    threads: int = 0,  # 0 = auto (use all cores)
) -> int:
    """
    Compress a file using Zstandard.
    Returns compressed size in bytes.
    threads=0 means zstd auto-detects CPU cores.
    """
    cctx = zstd.ZstdCompressor(level=level, threads=threads)
    compressed_size = 0

    with open(src_path, "rb") as src:
        with open(dst_path, "wb") as dst:
            compressor = cctx.stream_writer(dst)
            while True:
                chunk = src.read(STREAM_BUFFER)
                if not chunk:
                    break
                compressor.write(chunk)
            compressor.flush(zstd.FLUSH_FRAME)
            compressed_size = dst.tell()

    return compressed_size


def compress_file_gzip(
    src_path: str,
    dst_path: str,
    level: int = 6,
) -> int:
    """
    Compress a file using gzip (fallback if zstd not installed).
    Returns compressed size in bytes.
    """
    with open(src_path, "rb") as src:
        with gzip.open(dst_path, "wb", compresslevel=level) as dst:
            while True:
                chunk = src.read(STREAM_BUFFER)
                if not chunk:
                    break
                dst.write(chunk)

    return os.path.getsize(dst_path)


def compress_bytes_zstd(data: bytes, level: int = 3) -> bytes:
    """Compress raw bytes using zstd. For in-memory chunk compression."""
    cctx = zstd.ZstdCompressor(level=level)
    return cctx.compress(data)


def compress_bytes_gzip(data: bytes, level: int = 6) -> bytes:
    """Compress raw bytes using gzip. Fallback for in-memory compression."""
    return gzip.compress(data, compresslevel=level)


def compress_file(
    src_path: str,
    dst_path: str | None = None,
    decision: CompressionDecision | None = None,
) -> tuple[str, int, bool]:
    """
    Smart file compression pipeline.
    1. Analyze file entropy if no decision provided
    2. Skip if already compressed
    3. Compress with zstd (or gzip fallback) if beneficial
    4. Verify compression actually reduced size

    Returns: (output_path, output_size, was_compressed)
    """
    original_size = os.path.getsize(src_path)

    # Analyze if no decision provided
    if decision is None:
        decision = analyze_file(src_path)

    # Skip compression if analysis says so
    if not decision.should_compress:
        logger.debug(f"Skipping compression: {decision.reason}")
        return (src_path, original_size, False)

    # Determine output path
    if dst_path is None:
        ext = ".zst" if HAS_ZSTD else ".gz"
        dst_path = src_path + ext

    # Compress
    level = decision.level
    if HAS_ZSTD:
        compressed_size = compress_file_zstd(src_path, dst_path, level=level)
    else:
        # Map zstd levels to gzip levels (rough equivalence)
        gzip_level = min(9, max(1, level + 3))
        compressed_size = compress_file_gzip(src_path, dst_path, level=gzip_level)

    # Verify compression actually helped (must save at least 5%)
    ratio = compressed_size / original_size if original_size > 0 else 1.0
    if ratio > 0.95:
        # Compression didn't help — delete compressed file, use original
        logger.debug(
            f"Compression ineffective (ratio {ratio:.2%}) — using original"
        )
        Path(dst_path).unlink(missing_ok=True)
        return (src_path, original_size, False)

    savings_pct = (1 - ratio) * 100
    logger.debug(f"Compressed: {savings_pct:.1f}% smaller ({decision.reason})")
    return (dst_path, compressed_size, True)


def get_compression_ext() -> str:
    """Return the file extension for the active compressor."""
    return ".zst" if HAS_ZSTD else ".gz"


def get_compressor_name() -> str:
    """Return the name of the active compressor."""
    return "zstd" if HAS_ZSTD else "gzip"
