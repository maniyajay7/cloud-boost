"""
cloud-boost v3 — test suite for transport module
"""

import pytest
from unittest.mock import patch, MagicMock

from cloudboost.transport import (
    QuotaTracker,
    check_rclone_installed,
    get_rclone_remotes,
    build_rclone_flags,
)
from cloudboost.config import UploadConfig, GB, format_size


# ─── QuotaTracker tests ──────────────────────────────────────────────────────

class TestQuotaTracker:
    def test_initial_state(self):
        qt = QuotaTracker()
        assert qt.session_bytes == 0
        assert qt.check() == "ok"
        assert qt.remaining() == 750 * GB
        assert qt.usage_pct() == 0.0

    def test_add_bytes(self):
        qt = QuotaTracker()
        qt.add(100 * GB)
        assert qt.session_bytes == 100 * GB
        assert qt.check() == "ok"

    def test_warning_at_93_percent(self):
        qt = QuotaTracker()
        qt.add(int(750 * GB * 0.94))  # above 93%
        assert qt.check() == "warning"

    def test_blocked_at_limit(self):
        qt = QuotaTracker()
        qt.add(750 * GB)
        assert qt.check() == "blocked"

    def test_blocked_above_limit(self):
        qt = QuotaTracker()
        qt.add(800 * GB)
        assert qt.check() == "blocked"
        assert qt.remaining() == 0

    def test_non_gdrive_always_ok(self):
        qt = QuotaTracker(provider="s3")
        qt.add(1000 * GB)  # way over GDrive limit
        assert qt.check() == "ok"  # but not gdrive, so ok

    def test_usage_pct(self):
        qt = QuotaTracker()
        qt.add(375 * GB)  # half
        pct = qt.usage_pct()
        assert abs(pct - 50.0) < 0.1

    def test_remaining_decreases(self):
        qt = QuotaTracker()
        qt.add(200 * GB)
        assert qt.remaining() == 550 * GB


# ─── build_rclone_flags tests ────────────────────────────────────────────────

class TestBuildRcloneFlags:
    def test_default_gdrive_flags(self):
        config = UploadConfig()
        flags = build_rclone_flags(config)
        assert f"--transfers={config.rclone_transfers}" in flags
        assert f"--buffer-size={config.rclone_buffer_size}" in flags
        assert "--checkers=16" in flags
        assert "--fast-list" in flags
        assert f"--tpslimit={config.tps_limit}" in flags
        # GDrive-specific
        assert f"--drive-chunk-size={config.rclone_drive_chunk}" in flags
        assert "--drive-upload-cutoff=8M" in flags
        assert "--drive-use-trash=false" in flags
        assert "--drive-pacer-min-sleep=10ms" in flags
        assert "--drive-pacer-burst=200" in flags

    def test_s3_flags(self):
        config = UploadConfig(provider="s3")
        flags = build_rclone_flags(config)
        assert "--s3-upload-concurrency=8" in flags
        assert "--s3-chunk-size=64M" in flags
        # Should NOT have GDrive-specific flags
        assert "--drive-chunk-size=64M" not in flags

    def test_bandwidth_limit(self):
        config = UploadConfig(max_bandwidth=50 * 1024 * 1024)  # 50MB
        flags = build_rclone_flags(config)
        bw_flags = [f for f in flags if f.startswith("--bwlimit=")]
        assert len(bw_flags) == 1

    def test_no_bandwidth_limit_by_default(self):
        config = UploadConfig()
        flags = build_rclone_flags(config)
        bw_flags = [f for f in flags if f.startswith("--bwlimit=")]
        assert len(bw_flags) == 0

    def test_max_transfer_flag(self):
        config = UploadConfig(max_transfer=700 * GB)
        flags = build_rclone_flags(config)
        mt_flags = [f for f in flags if f.startswith("--max-transfer=")]
        assert len(mt_flags) == 1

    def test_verbose_flag(self):
        config = UploadConfig(verbose=True)
        flags = build_rclone_flags(config)
        assert "--verbose" in flags

    def test_no_verbose_by_default(self):
        config = UploadConfig()
        flags = build_rclone_flags(config)
        assert "--verbose" not in flags


# ─── rclone checks ───────────────────────────────────────────────────────────

class TestRcloneChecks:
    def test_check_rclone_installed_returns_bool(self):
        result = check_rclone_installed()
        assert isinstance(result, bool)

    def test_get_rclone_remotes_returns_list(self):
        result = get_rclone_remotes()
        assert isinstance(result, list)
