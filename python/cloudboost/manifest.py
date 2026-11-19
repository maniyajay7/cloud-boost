"""
cloud-boost v3 — manifest module
File-level upload tracking with integrity verification.
Replaces the per-chunk checkpoint system with smarter file-level manifests.
"""

import json
import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone

from cloudboost.config import CHECKPOINTS_DIR, ensure_dirs

MANIFEST_VERSION = 3


def _manifest_key(filepath: str) -> str:
    """Generate a unique key for a file based on absolute path."""
    abs_path = str(Path(filepath).resolve())
    return hashlib.md5(abs_path.encode()).hexdigest()[:16]


def get_manifest_path(filepath: str) -> Path:
    """Get manifest path for a given source file."""
    ensure_dirs()
    key = _manifest_key(filepath)
    name = Path(filepath).stem[:32]  # truncate long names
    return CHECKPOINTS_DIR / f"{name}_{key}.manifest.json"


def create_manifest(filepath: str) -> dict:
    """Create a fresh manifest for a file."""
    return {
        "version": MANIFEST_VERSION,
        "source_file": str(Path(filepath).resolve()),
        "source_name": Path(filepath).name,
        "source_size": os.path.getsize(filepath),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",  # pending | uploading | completed | failed
        "compressed": False,
        "compressed_path": "",
        "compressed_size": 0,
        "compression_ratio": 0.0,
        "compressor": "",
        "destination": "",
        "upload_started_at": "",
        "upload_completed_at": "",
        "bytes_uploaded": 0,
        "error": "",
    }


def load_manifest(filepath: str) -> dict | None:
    """Load existing manifest for a file. Returns None if not found."""
    manifest_path = get_manifest_path(filepath)
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            if data.get("version") == MANIFEST_VERSION:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_manifest(filepath: str, manifest: dict) -> None:
    """Save manifest to disk."""
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path = get_manifest_path(filepath)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def mark_uploading(filepath: str, manifest: dict, destination: str) -> None:
    """Mark file as currently uploading."""
    manifest["status"] = "uploading"
    manifest["destination"] = destination
    manifest["upload_started_at"] = datetime.now(timezone.utc).isoformat()
    save_manifest(filepath, manifest)


def mark_completed(filepath: str, manifest: dict) -> None:
    """Mark file as successfully uploaded."""
    manifest["status"] = "completed"
    manifest["upload_completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["bytes_uploaded"] = manifest.get("compressed_size") or manifest.get("source_size", 0)
    save_manifest(filepath, manifest)


def mark_failed(filepath: str, manifest: dict, error: str) -> None:
    """Mark file as failed with error message."""
    manifest["status"] = "failed"
    manifest["error"] = error[:500]
    save_manifest(filepath, manifest)


def mark_compressed(
    filepath: str,
    manifest: dict,
    compressed_path: str,
    compressed_size: int,
    compressor: str,
) -> None:
    """Record compression result in manifest."""
    original_size = manifest.get("source_size", 0)
    manifest["compressed"] = True
    manifest["compressed_path"] = compressed_path
    manifest["compressed_size"] = compressed_size
    manifest["compressor"] = compressor
    manifest["compression_ratio"] = (
        round((1 - compressed_size / original_size) * 100, 1) if original_size > 0 else 0
    )
    save_manifest(filepath, manifest)


def clear_manifest(filepath: str) -> None:
    """Remove manifest after successful upload."""
    manifest_path = get_manifest_path(filepath)
    manifest_path.unlink(missing_ok=True)


def is_already_uploaded(filepath: str, destination: str) -> bool:
    """Check if a file was already successfully uploaded to the same destination."""
    manifest = load_manifest(filepath)
    if manifest is None:
        return False
    return (
        manifest.get("status") == "completed"
        and manifest.get("destination") == destination
        and manifest.get("source_size") == os.path.getsize(filepath)
    )


def list_pending_uploads() -> list[dict]:
    """List all manifests that are in 'uploading' or 'failed' state (resumable)."""
    ensure_dirs()
    pending = []
    for f in CHECKPOINTS_DIR.glob("*.manifest.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if data.get("status") in ("uploading", "failed"):
                pending.append(data)
        except Exception:
            continue
    return pending
