"""
cloud-boost v3 — test suite for config module
"""

import pytest
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from cloudboost.config import (
    KB, MB, GB,
    parse_size,
    format_size,
    format_duration,
    UploadConfig,
    ensure_dirs,
    load_config,
    save_default_config,
    PROVIDERS,
)


# ─── parse_size tests ─────────────────────────────────────────────────────────

class TestParseSize:
    def test_bytes(self):
        assert parse_size("1024B") == 1024

    def test_kilobytes(self):
        assert parse_size("5KB") == 5 * KB

    def test_megabytes(self):
        assert parse_size("8MB") == 8 * MB

    def test_gigabytes(self):
        assert parse_size("2GB") == 2 * GB

    def test_float_megabytes(self):
        assert parse_size("1.5MB") == int(1.5 * MB)

    def test_plain_number(self):
        assert parse_size("4096") == 4096

    def test_whitespace(self):
        assert parse_size("  10MB  ") == 10 * MB

    def test_case_insensitive(self):
        assert parse_size("5mb") == 5 * MB
        assert parse_size("5Mb") == 5 * MB


# ─── format_size tests ────────────────────────────────────────────────────────

class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500 B"

    def test_kilobytes(self):
        result = format_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = format_size(10 * MB)
        assert "10.00 MB" == result

    def test_gigabytes(self):
        result = format_size(2 * GB)
        assert "2.00 GB" == result


# ─── format_duration tests ────────────────────────────────────────────────────

class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(30.5) == "30.5s"

    def test_minutes(self):
        result = format_duration(90)
        assert "1m" in result
        assert "30s" in result

    def test_hours(self):
        result = format_duration(3700)
        assert "1h" in result


# ─── UploadConfig tests ───────────────────────────────────────────────────────

class TestUploadConfig:
    def test_default_values(self):
        config = UploadConfig()
        assert config.threads == 6
        assert config.chunk_size == 8 * MB
        assert config.compression is True
        assert config.provider == "gdrive"
        assert config.tps_limit == 10
        assert config.max_transfer == 0
        assert config.dedup is True

    def test_custom_values(self):
        config = UploadConfig(threads=4, provider="s3")
        assert config.threads == 4
        assert config.provider == "s3"

    def test_invalid_threads_low(self):
        with pytest.raises(ValueError, match="Threads must be 1-32"):
            UploadConfig(threads=0)

    def test_invalid_threads_high(self):
        with pytest.raises(ValueError, match="Threads must be 1-32"):
            UploadConfig(threads=64)

    def test_invalid_chunk_size_low(self):
        with pytest.raises(ValueError, match="Chunk size must be >= 1MB"):
            UploadConfig(chunk_size=100)

    def test_invalid_chunk_size_high(self):
        with pytest.raises(ValueError, match="Chunk size must be <= 512MB"):
            UploadConfig(chunk_size=1 * GB)

    def test_invalid_compression_level(self):
        with pytest.raises(ValueError, match="Compression level must be 1-9"):
            UploadConfig(compression_level=0)

    def test_invalid_provider(self):
        with pytest.raises(ValueError, match="Provider must be one of"):
            UploadConfig(provider="ftp")

    def test_invalid_max_retries(self):
        with pytest.raises(ValueError, match="Max retries must be 0-10"):
            UploadConfig(max_retries=20)


# ─── auto_tune tests ──────────────────────────────────────────────────────────

class TestAutoTune:
    def test_small_file(self):
        config = UploadConfig.auto_tune(50 * MB)
        assert config.threads == 4
        assert config.rclone_transfers == 4
        assert config.rclone_drive_chunk == "64M"

    def test_medium_file(self):
        config = UploadConfig.auto_tune(500 * MB)
        assert config.threads == 6
        assert config.rclone_transfers == 6
        assert config.rclone_drive_chunk == "128M"

    def test_large_file(self):
        config = UploadConfig.auto_tune(5 * GB)
        assert config.threads == 8
        assert config.rclone_transfers == 8

    def test_huge_file(self):
        config = UploadConfig.auto_tune(50 * GB)
        assert config.threads == 12
        assert config.rclone_drive_chunk == "256M"
        assert config.compression_level == 2

    def test_overrides_win(self):
        config = UploadConfig.auto_tune(50 * GB, rclone_transfers=4)
        assert config.rclone_transfers == 4  # override wins


# ─── ensure_dirs / config file tests ──────────────────────────────────────────

class TestConfigFiles:
    def test_ensure_dirs_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cloudboost.config.CONFIG_DIR", Path(tmpdir) / "cloudboost"):
                with patch("cloudboost.config.CHECKPOINTS_DIR", Path(tmpdir) / "cloudboost" / "checkpoints"):
                    with patch("cloudboost.config.LOGS_DIR", Path(tmpdir) / "cloudboost" / "logs"):
                        ensure_dirs()
                        assert (Path(tmpdir) / "cloudboost").exists()
                        assert (Path(tmpdir) / "cloudboost" / "checkpoints").exists()
                        assert (Path(tmpdir) / "cloudboost" / "logs").exists()


# ─── providers list ───────────────────────────────────────────────────────────

class TestProviders:
    def test_gdrive_in_providers(self):
        assert "gdrive" in PROVIDERS

    def test_s3_in_providers(self):
        assert "s3" in PROVIDERS

    def test_provider_count(self):
        assert len(PROVIDERS) == 6
