"""
cloud-boost integrity module
Handles: SHA-256 checksums, file deduplication, chunk verification
"""

import hashlib
import json
from pathlib import Path
from cloudboost.config import CHECKPOINTS_DIR, format_size

HASH_BUFFER = 65536  # 64KB read buffer for hashing


def compute_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file. Streams to avoid loading into memory."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            data = f.read(HASH_BUFFER)
            if not data:
                break
            sha.update(data)
    return sha.hexdigest()


def compute_chunk_hash(data: bytes) -> str:
    """Compute SHA-256 hash of raw chunk bytes."""
    return hashlib.sha256(data).hexdigest()


def get_manifest_path(filepath: str) -> Path:
    """Get the manifest file path for a given source file."""
    file_hash = hashlib.md5(filepath.encode()).hexdigest()[:12]
    name = Path(filepath).stem
    return CHECKPOINTS_DIR / f"{name}_{file_hash}.manifest.json"


def load_manifest(filepath: str) -> dict:
    """Load upload manifest (chunk hashes + upload state)."""
    manifest_path = get_manifest_path(filepath)
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {
        "source_file": filepath,
        "source_hash": "",
        "total_chunks": 0,
        "chunk_size": 0,
        "chunks": {},
        "completed": False,
    }


def save_manifest(filepath: str, manifest: dict) -> None:
    """Save upload manifest to disk."""
    manifest_path = get_manifest_path(filepath)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def clear_manifest(filepath: str) -> None:
    """Remove manifest after successful upload."""
    manifest_path = get_manifest_path(filepath)
    if manifest_path.exists():
        manifest_path.unlink()


def is_chunk_uploaded(manifest: dict, chunk_index: int, chunk_hash: str) -> bool:
    """Check if a specific chunk was already uploaded with matching hash."""
    chunk_key = str(chunk_index)
    if chunk_key in manifest["chunks"]:
        entry = manifest["chunks"][chunk_key]
        return entry.get("uploaded", False) and entry.get("hash") == chunk_hash
    return False


def mark_chunk_uploaded(manifest: dict, chunk_index: int, chunk_hash: str, compressed_size: int) -> None:
    """Mark a chunk as successfully uploaded in the manifest."""
    manifest["chunks"][str(chunk_index)] = {
        "hash": chunk_hash,
        "compressed_size": compressed_size,
        "uploaded": True,
    }


def file_changed(filepath: str, manifest: dict) -> bool:
    """Check if the source file has changed since last upload attempt."""
    if not manifest["source_hash"]:
        return True
    current_hash = compute_sha256(filepath)
    return current_hash != manifest["source_hash"]
