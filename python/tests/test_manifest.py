"""
cloud-boost v3 — test suite for manifest module
"""

import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from cloudboost.manifest import (
    MANIFEST_VERSION,
    get_manifest_path,
    create_manifest,
    load_manifest,
    save_manifest,
    mark_uploading,
    mark_completed,
    mark_failed,
    mark_compressed,
    clear_manifest,
    is_already_uploaded,
    list_pending_uploads,
)
from cloudboost.config import CHECKPOINTS_DIR


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _create_temp_file(data: bytes = b"test data", suffix: str = "") -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(data)
    f.close()
    return f.name


# ─── create_manifest tests ────────────────────────────────────────────────────

class TestCreateManifest:
    def test_creates_valid_manifest(self):
        path = _create_temp_file(b"hello world" * 100)
        try:
            manifest = create_manifest(path)
            assert manifest["version"] == MANIFEST_VERSION
            assert manifest["source_name"] == Path(path).name
            assert manifest["source_size"] == os.path.getsize(path)
            assert manifest["status"] == "pending"
            assert manifest["compressed"] is False
            assert manifest["destination"] == ""
            assert manifest["error"] == ""
        finally:
            os.unlink(path)

    def test_records_absolute_path(self):
        path = _create_temp_file(b"data")
        try:
            manifest = create_manifest(path)
            assert os.path.isabs(manifest["source_file"])
        finally:
            os.unlink(path)

    def test_has_timestamps(self):
        path = _create_temp_file(b"data")
        try:
            manifest = create_manifest(path)
            assert "created_at" in manifest
            assert "updated_at" in manifest
            assert len(manifest["created_at"]) > 10  # ISO format
        finally:
            os.unlink(path)


# ─── save/load manifest tests ────────────────────────────────────────────────

class TestSaveLoadManifest:
    def test_save_and_load_roundtrip(self):
        path = _create_temp_file(b"test data for manifest")
        try:
            manifest = create_manifest(path)
            save_manifest(path, manifest)
            loaded = load_manifest(path)
            assert loaded is not None
            assert loaded["version"] == MANIFEST_VERSION
            assert loaded["source_name"] == manifest["source_name"]
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_load_nonexistent_returns_none(self):
        result = load_manifest("/nonexistent/path/that/does/not/exist.bin")
        assert result is None

    def test_load_wrong_version_returns_none(self):
        path = _create_temp_file(b"test")
        try:
            manifest = create_manifest(path)
            manifest["version"] = 999  # wrong version
            save_manifest(path, manifest)
            loaded = load_manifest(path)
            assert loaded is None
        finally:
            os.unlink(path)
            clear_manifest(path)


# ─── mark_ status transitions ────────────────────────────────────────────────

class TestStatusTransitions:
    def test_mark_uploading(self):
        path = _create_temp_file(b"data" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/backup/")
            assert manifest["status"] == "uploading"
            assert manifest["destination"] == "gdrive:/backup/"
            assert manifest["upload_started_at"] != ""
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_mark_completed(self):
        path = _create_temp_file(b"data" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/test/")
            mark_completed(path, manifest)
            assert manifest["status"] == "completed"
            assert manifest["upload_completed_at"] != ""
            assert manifest["bytes_uploaded"] > 0
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_mark_failed(self):
        path = _create_temp_file(b"data")
        try:
            manifest = create_manifest(path)
            mark_failed(path, manifest, "Connection timeout")
            assert manifest["status"] == "failed"
            assert manifest["error"] == "Connection timeout"
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_mark_failed_truncates_long_error(self):
        path = _create_temp_file(b"data")
        try:
            manifest = create_manifest(path)
            long_error = "x" * 1000
            mark_failed(path, manifest, long_error)
            assert len(manifest["error"]) == 500
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_mark_compressed(self):
        path = _create_temp_file(b"data" * 1000)
        try:
            manifest = create_manifest(path)
            original_size = manifest["source_size"]
            compressed_size = original_size // 2
            mark_compressed(path, manifest, "/tmp/data.zst", compressed_size, "zstd")
            assert manifest["compressed"] is True
            assert manifest["compressed_path"] == "/tmp/data.zst"
            assert manifest["compressed_size"] == compressed_size
            assert manifest["compressor"] == "zstd"
            assert manifest["compression_ratio"] > 0
        finally:
            os.unlink(path)
            clear_manifest(path)


# ─── clear_manifest tests ────────────────────────────────────────────────────

class TestClearManifest:
    def test_clears_existing(self):
        path = _create_temp_file(b"data")
        try:
            manifest = create_manifest(path)
            save_manifest(path, manifest)
            manifest_path = get_manifest_path(path)
            assert manifest_path.exists()
            clear_manifest(path)
            assert not manifest_path.exists()
        finally:
            os.unlink(path)

    def test_clears_nonexistent_no_error(self):
        # Should not raise
        clear_manifest("/totally/fake/path.bin")


# ─── is_already_uploaded tests ────────────────────────────────────────────────

class TestIsAlreadyUploaded:
    def test_not_uploaded(self):
        path = _create_temp_file(b"new file")
        try:
            assert is_already_uploaded(path, "gdrive:/test/") is False
        finally:
            os.unlink(path)

    def test_completed_upload_detected(self):
        path = _create_temp_file(b"uploaded data" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/dest/")
            mark_completed(path, manifest)
            assert is_already_uploaded(path, "gdrive:/dest/") is True
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_different_destination_not_matched(self):
        path = _create_temp_file(b"data" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/dest1/")
            mark_completed(path, manifest)
            # Different destination
            assert is_already_uploaded(path, "gdrive:/dest2/") is False
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_failed_upload_not_matched(self):
        path = _create_temp_file(b"data" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/dest/")
            mark_failed(path, manifest, "error")
            assert is_already_uploaded(path, "gdrive:/dest/") is False
        finally:
            os.unlink(path)
            clear_manifest(path)


# ─── list_pending_uploads tests ───────────────────────────────────────────────

class TestListPendingUploads:
    def test_returns_list(self):
        result = list_pending_uploads()
        assert isinstance(result, list)

    def test_finds_uploading_manifest(self):
        path = _create_temp_file(b"pending upload test" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/test/")
            pending = list_pending_uploads()
            names = [m.get("source_name") for m in pending]
            assert Path(path).name in names
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_finds_failed_manifest(self):
        path = _create_temp_file(b"failed upload test" * 100)
        try:
            manifest = create_manifest(path)
            mark_failed(path, manifest, "test error")
            pending = list_pending_uploads()
            names = [m.get("source_name") for m in pending]
            assert Path(path).name in names
        finally:
            os.unlink(path)
            clear_manifest(path)

    def test_ignores_completed(self):
        path = _create_temp_file(b"completed test" * 100)
        try:
            manifest = create_manifest(path)
            mark_uploading(path, manifest, "gdrive:/test/")
            mark_completed(path, manifest)
            pending = list_pending_uploads()
            names = [m.get("source_name") for m in pending]
            assert Path(path).name not in names
        finally:
            os.unlink(path)
            clear_manifest(path)
