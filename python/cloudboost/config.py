"""
cloud-boost configuration manager
Handles: loading, validation, defaults, user overrides
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field

CONFIG_DIR = Path.home() / ".cloudboost"
CONFIG_FILE = CONFIG_DIR / "config.yml"
CHECKPOINTS_DIR = CONFIG_DIR / "checkpoints"
LOGS_DIR = CONFIG_DIR / "logs"

# ─── Size Constants ───────────────────────────────────────────────────────────
KB = 1024
MB = 1024 * KB
GB = 1024 * MB

# ─── Supported Providers ─────────────────────────────────────────────────────
PROVIDERS = ["gdrive", "s3", "azure", "dropbox", "spaces", "supabase"]


def parse_size(size_str: str) -> int:
    """Parse human-readable size string to bytes. e.g. '5MB' → 5242880"""
    size_str = size_str.strip().upper()
    multipliers = {"B": 1, "KB": KB, "MB": MB, "GB": GB}
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(suffix):
            return int(float(size_str[: -len(suffix)].strip()) * mult)
    return int(size_str)


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if size_bytes >= GB:
        return f"{size_bytes / GB:.2f} GB"
    if size_bytes >= MB:
        return f"{size_bytes / MB:.2f} MB"
    if size_bytes >= KB:
        return f"{size_bytes / KB:.2f} KB"
    return f"{size_bytes} B"


def format_duration(seconds: float) -> str:
    """Format seconds to human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hours}h {mins}m"


@dataclass
class UploadConfig:
    """Upload configuration with validated defaults."""

    threads: int = 6
    chunk_size: int = 8 * MB          # 8MB default — optimal for GDrive
    compression: bool = True
    compression_level: int = 6        # gzip level 1-9 (6 = balanced)
    resume: bool = True
    provider: str = "gdrive"
    max_bandwidth: int = 0            # 0 = unlimited (bytes/sec)
    max_retries: int = 3
    retry_delay: float = 2.0          # seconds between retries
    checksum_verify: bool = True      # SHA-256 integrity check
    rclone_transfers: int = 8         # rclone --transfers flag
    rclone_buffer_size: str = "256M"  # rclone --buffer-size flag
    rclone_drive_chunk: str = "64M"   # rclone --drive-chunk-size
    dedup: bool = True                # skip unchanged files
    log_file: str = ""
    verbose: bool = False

    def __post_init__(self) -> None:
        if self.threads < 1 or self.threads > 32:
            raise ValueError(f"Threads must be 1-32, got {self.threads}")
        if self.chunk_size < 1 * MB:
            raise ValueError(f"Chunk size must be >= 1MB, got {format_size(self.chunk_size)}")
        if self.chunk_size > 512 * MB:
            raise ValueError(f"Chunk size must be <= 512MB, got {format_size(self.chunk_size)}")
        if self.compression_level < 1 or self.compression_level > 9:
            raise ValueError(f"Compression level must be 1-9, got {self.compression_level}")
        if self.max_retries < 0 or self.max_retries > 10:
            raise ValueError(f"Max retries must be 0-10, got {self.max_retries}")
        if self.provider not in PROVIDERS:
            raise ValueError(f"Provider must be one of {PROVIDERS}, got {self.provider}")

    @classmethod
    def auto_tune(cls, file_size: int, **overrides) -> "UploadConfig":
        """
        Automatically tune configuration based on file size.
        Small files (<100MB)   → fewer threads, smaller chunks
        Medium files (<1GB)    → balanced
        Large files (<10GB)    → more threads, larger chunks
        Huge files (>10GB)     → max parallelism, large chunks, lower compression
        """
        config = cls()

        if file_size < 100 * MB:
            config.threads = 4
            config.chunk_size = 4 * MB
            config.compression_level = 6
        elif file_size < 1 * GB:
            config.threads = 6
            config.chunk_size = 8 * MB
            config.compression_level = 6
        elif file_size < 10 * GB:
            config.threads = 8
            config.chunk_size = 16 * MB
            config.compression_level = 4
            config.rclone_transfers = 12
        else:
            config.threads = 12
            config.chunk_size = 32 * MB
            config.compression_level = 2  # speed over ratio
            config.rclone_transfers = 16
            config.rclone_drive_chunk = "128M"

        # Apply user overrides last — user always wins
        for key, val in overrides.items():
            if hasattr(config, key) and val is not None:
                setattr(config, key, val)

        return config


def ensure_dirs() -> None:
    """Create required directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load user config from ~/.cloudboost/config.yml"""
    ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_default_config() -> None:
    """Write default config file if it doesn't exist."""
    ensure_dirs()
    if not CONFIG_FILE.exists():
        defaults = {
            "threads": 6,
            "chunk_size": "8MB",
            "compression": True,
            "compression_level": 6,
            "resume": True,
            "provider": "gdrive",
            "max_bandwidth": 0,
            "max_retries": 3,
            "checksum_verify": True,
            "dedup": True,
            "verbose": False,
        }
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(defaults, f, default_flow_style=False)
