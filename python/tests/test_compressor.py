"""
cloud-boost v3 — test suite for compressor module
"""

import os
import gzip
import tempfile
import pytest

from cloudboost.compressor import (
    compress_file_gzip,
    compress_file,
    get_compressor_name,
    get_compression_ext,
    HAS_ZSTD,
    STREAM_BUFFER,
)
from cloudboost.entropy import CompressionDecision


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _create_temp_file(data: bytes, suffix: str = "") -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(data)
    f.close()
    return f.name


def _compressible_data(size: int = 50000) -> bytes:
    """Generate highly compressible data."""
    return b"The quick brown fox jumps over the lazy dog.\n" * (size // 46 + 1)


def _incompressible_data(size: int = 50000) -> bytes:
    """Generate data that won't compress well (pseudo-random)."""
    import random
    random.seed(42)
    return bytes(random.getrandbits(8) for _ in range(size))


# ─── compress_file_gzip tests ────────────────────────────────────────────────

class TestCompressFileGzip:
    def test_compresses_text_file(self):
        src = _create_temp_file(_compressible_data())
        dst = src + ".gz"
        try:
            compressed_size = compress_file_gzip(src, dst)
            original_size = os.path.getsize(src)
            assert compressed_size < original_size
            assert os.path.exists(dst)
            # Verify it's valid gzip
            with gzip.open(dst, "rb") as f:
                decompressed = f.read()
            assert decompressed == _compressible_data()
        finally:
            os.unlink(src)
            if os.path.exists(dst):
                os.unlink(dst)

    def test_returns_size(self):
        src = _create_temp_file(_compressible_data(10000))
        dst = src + ".gz"
        try:
            size = compress_file_gzip(src, dst)
            assert size == os.path.getsize(dst)
            assert size > 0
        finally:
            os.unlink(src)
            if os.path.exists(dst):
                os.unlink(dst)


# ─── compress_file smart pipeline tests ───────────────────────────────────────

class TestCompressFile:
    def test_skip_when_not_compressible(self):
        data = _compressible_data()
        src = _create_temp_file(data)
        decision = CompressionDecision(
            should_compress=False,
            reason="Already compressed",
        )
        try:
            output_path, output_size, was_compressed = compress_file(src, decision=decision)
            assert was_compressed is False
            assert output_path == src
            assert output_size == os.path.getsize(src)
        finally:
            os.unlink(src)

    def test_compress_compressible_file(self):
        data = _compressible_data(100000)
        src = _create_temp_file(data, suffix=".txt")
        decision = CompressionDecision(
            should_compress=True,
            reason="Low entropy",
            level=3,
        )
        dst = src + get_compression_ext()
        try:
            output_path, output_size, was_compressed = compress_file(src, dst, decision)
            assert was_compressed is True
            assert output_size < os.path.getsize(src)
            assert output_path == dst
        finally:
            os.unlink(src)
            if os.path.exists(dst):
                os.unlink(dst)

    def test_skip_when_no_savings(self):
        # Random data won't compress — should fall back to original
        data = _incompressible_data(50000)
        src = _create_temp_file(data, suffix=".bin")
        decision = CompressionDecision(
            should_compress=True,
            reason="Medium entropy",
            level=1,
        )
        dst = src + ".gz"
        try:
            output_path, output_size, was_compressed = compress_file(src, dst, decision)
            assert was_compressed is False  # ratio > 0.95
            assert output_path == src
            assert not os.path.exists(dst)  # cleaned up
        finally:
            os.unlink(src)

    def test_auto_analyze_when_no_decision(self):
        # Create a text file — should auto-detect as compressible
        data = _compressible_data(100000)
        src = _create_temp_file(data, suffix=".log")
        dst = src + get_compression_ext()
        try:
            output_path, output_size, was_compressed = compress_file(src, dst)
            # Text file should be compressible
            assert was_compressed is True
        finally:
            os.unlink(src)
            if os.path.exists(dst):
                os.unlink(dst)


# ─── compressor metadata tests ───────────────────────────────────────────────

class TestCompressorMeta:
    def test_compressor_name(self):
        name = get_compressor_name()
        assert name in ("zstd", "gzip")

    def test_compression_ext(self):
        ext = get_compression_ext()
        assert ext in (".zst", ".gz")

    def test_ext_matches_name(self):
        name = get_compressor_name()
        ext = get_compression_ext()
        if name == "zstd":
            assert ext == ".zst"
        else:
            assert ext == ".gz"

    def test_stream_buffer_is_1mb(self):
        assert STREAM_BUFFER == 1024 * 1024
