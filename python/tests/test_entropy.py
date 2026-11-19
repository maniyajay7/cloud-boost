"""
cloud-boost v3 — test suite for entropy module
"""

import os
import math
import tempfile
import pytest

from cloudboost.entropy import (
    check_magic_bytes,
    check_extension,
    compute_shannon_entropy,
    sample_entropy,
    CompressionDecision,
    analyze_file,
    ENTROPY_HIGH,
    ENTROPY_LOW,
)


# ─── Helper: create temp file with specific bytes ─────────────────────────────

def _create_temp_file(data: bytes, suffix: str = "") -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(data)
    f.close()
    return f.name


# ─── check_magic_bytes tests ──────────────────────────────────────────────────

class TestCheckMagicBytes:
    def test_zip_magic(self):
        path = _create_temp_file(b"PK\x03\x04" + b"\x00" * 100)
        assert check_magic_bytes(path) == "zip"
        os.unlink(path)

    def test_gzip_magic(self):
        path = _create_temp_file(b"\x1f\x8b\x08" + b"\x00" * 100)
        assert check_magic_bytes(path) == "gzip"
        os.unlink(path)

    def test_png_magic(self):
        path = _create_temp_file(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert check_magic_bytes(path) == "png"
        os.unlink(path)

    def test_jpeg_magic(self):
        path = _create_temp_file(b"\xff\xd8\xff" + b"\x00" * 100)
        assert check_magic_bytes(path) == "jpeg"
        os.unlink(path)

    def test_7z_magic(self):
        path = _create_temp_file(b"\x37\x7a\xbc\xaf\x27\x1c" + b"\x00" * 100)
        assert check_magic_bytes(path) == "7z"
        os.unlink(path)

    def test_zstd_magic(self):
        path = _create_temp_file(b"\x28\xb5\x2f\xfd" + b"\x00" * 100)
        assert check_magic_bytes(path) == "zstd"
        os.unlink(path)

    def test_exe_magic(self):
        path = _create_temp_file(b"MZ" + b"\x00" * 100)
        assert check_magic_bytes(path) == "exe_dll"
        os.unlink(path)

    def test_unknown_magic(self):
        path = _create_temp_file(b"HELLO WORLD THIS IS TEXT" * 10)
        assert check_magic_bytes(path) is None
        os.unlink(path)

    def test_empty_file(self):
        path = _create_temp_file(b"")
        assert check_magic_bytes(path) is None
        os.unlink(path)

    def test_tiny_file(self):
        path = _create_temp_file(b"\x00")
        assert check_magic_bytes(path) is None
        os.unlink(path)


# ─── check_extension tests ────────────────────────────────────────────────────

class TestCheckExtension:
    def test_zip_extension(self):
        assert check_extension("file.zip") is True

    def test_mp4_extension(self):
        assert check_extension("video.mp4") is True

    def test_pak_extension(self):
        assert check_extension("game.pak") is True

    def test_txt_extension(self):
        assert check_extension("readme.txt") is False

    def test_py_extension(self):
        assert check_extension("script.py") is False

    def test_case_insensitive(self):
        assert check_extension("file.ZIP") is True
        assert check_extension("file.Mp4") is True


# ─── compute_shannon_entropy tests ────────────────────────────────────────────

class TestShannonEntropy:
    def test_empty_data(self):
        assert compute_shannon_entropy(b"") == 0.0

    def test_single_byte_repeated(self):
        # All same bytes = 0 entropy
        data = bytes([0x41] * 1000)
        assert compute_shannon_entropy(data) == 0.0

    def test_two_equal_bytes(self):
        # Two equally distributed bytes = 1.0 entropy
        data = bytes([0x00, 0x01] * 500)
        assert abs(compute_shannon_entropy(data) - 1.0) < 0.01

    def test_random_data_high_entropy(self):
        # Random-ish data should have high entropy (near 8.0)
        import random
        random.seed(42)
        data = bytes(random.getrandbits(8) for _ in range(10000))
        entropy = compute_shannon_entropy(data)
        assert entropy > 7.0

    def test_text_data_medium_entropy(self):
        # English text has medium entropy (~4-5)
        data = b"The quick brown fox jumps over the lazy dog. " * 100
        entropy = compute_shannon_entropy(data)
        assert 3.0 < entropy < 6.0

    def test_all_256_bytes_equal(self):
        # Uniform distribution of all 256 byte values = 8.0 entropy
        data = bytes(range(256)) * 100
        entropy = compute_shannon_entropy(data)
        assert abs(entropy - 8.0) < 0.01


# ─── sample_entropy tests ─────────────────────────────────────────────────────

class TestSampleEntropy:
    def test_empty_file(self):
        path = _create_temp_file(b"")
        assert sample_entropy(path) == 0.0
        os.unlink(path)

    def test_small_file_reads_all(self):
        data = b"Hello World" * 10
        path = _create_temp_file(data)
        entropy = sample_entropy(path)
        expected = compute_shannon_entropy(data)
        assert abs(entropy - expected) < 0.01
        os.unlink(path)

    def test_large_file_samples(self):
        # Create a large file (> 12KB) with known pattern
        data = b"AAAA" * 5000 + bytes(range(256)) * 100 + b"BBBB" * 5000
        path = _create_temp_file(data)
        entropy = sample_entropy(path)
        assert entropy > 0.0  # Should have some entropy from middle section
        os.unlink(path)


# ─── CompressionDecision tests ────────────────────────────────────────────────

class TestCompressionDecision:
    def test_skip_decision(self):
        d = CompressionDecision(
            should_compress=False,
            reason="Known compressed format: zip",
            format_detected="zip",
            entropy=8.0,
        )
        assert d.should_compress is False
        assert "zip" in d.reason
        assert "SKIP" in repr(d)

    def test_compress_decision(self):
        d = CompressionDecision(
            should_compress=True,
            reason="Low entropy (3.50)",
            entropy=3.5,
            level=3,
        )
        assert d.should_compress is True
        assert d.level == 3
        assert "COMPRESS" in repr(d)


# ─── analyze_file integration tests ──────────────────────────────────────────

class TestAnalyzeFile:
    def test_zip_file_skip(self):
        path = _create_temp_file(b"PK\x03\x04" + b"\x00" * 1000, suffix=".zip")
        decision = analyze_file(path)
        assert decision.should_compress is False
        assert decision.format_detected == "zip"
        os.unlink(path)

    def test_png_file_skip(self):
        path = _create_temp_file(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000, suffix=".png")
        decision = analyze_file(path)
        assert decision.should_compress is False
        os.unlink(path)

    def test_txt_file_compress(self):
        # Repetitive text = low entropy = should compress
        data = b"hello world this is a test file with lots of repetition\n" * 500
        path = _create_temp_file(data, suffix=".txt")
        decision = analyze_file(path)
        assert decision.should_compress is True
        assert decision.level >= 1
        os.unlink(path)

    def test_compressed_extension_skip(self):
        # File with .mp4 extension but unknown magic bytes → extension check catches it
        path = _create_temp_file(b"\x00\x01\x02\x03" * 100, suffix=".mp4")
        decision = analyze_file(path)
        assert decision.should_compress is False
        os.unlink(path)

    def test_exe_falls_through_to_entropy(self):
        # EXE magic bytes should fall through to entropy check (mixed format)
        data = b"MZ" + b"\x00" * 500 + b"A" * 5000
        path = _create_temp_file(data, suffix=".exe")
        decision = analyze_file(path)
        # Should have been analyzed via entropy, not just magic bytes
        assert decision.entropy > 0.0 or decision.should_compress is True
        os.unlink(path)
