"""
cloud-boost v3 — entropy detection module
Fast compressed format detection via magic bytes + Shannon entropy sampling.
Decides whether compression will help BEFORE wasting CPU on it.
"""

import math
import os
from pathlib import Path

# ─── Magic Byte Signatures (known compressed/encrypted formats) ──────────────
# Format: (magic_bytes, format_name)
MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    # Archives (already compressed internally)
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip_empty"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x37\x7a\xbc\xaf\x27\x1c", "7z"),
    (b"\x1f\x8b\x08", "gzip"),
    (b"BZh", "bzip2"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\xfd\x37\x7a\x58\x5a\x00", "xz"),
    (b"\x04\x22\x4d\x18", "lz4"),
    # Media (already compressed)
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"\x1a\x45\xdf\xa3", "webm_mkv"),
    (b"\x00\x00\x00\x1c\x66\x74\x79\x70", "mp4"),
    (b"\x00\x00\x00\x20\x66\x74\x79\x70", "mp4"),
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\xff\xf3", "mp3"),
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"RIFF", "wav_avi"),  # could be uncompressed WAV — entropy will decide
    # Game-specific formats (typically pre-compressed)
    (b"\xc1\x83\x2a\x9e", "unreal_pak"),
    # Executables (mixed — entropy will decide)
    (b"MZ", "exe_dll"),
    (b"\x7fELF", "elf"),
]

# File extensions known to be already compressed (fallback if no magic match)
COMPRESSED_EXTENSIONS: set[str] = {
    ".zip", ".gz", ".gzip", ".bz2", ".xz", ".lz", ".lz4", ".zst", ".zstd",
    ".7z", ".rar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz",
    ".cab", ".lzma", ".lzo",
    # Media
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic",
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv", ".flv",
    ".mp3", ".aac", ".ogg", ".opus", ".wma", ".m4a", ".flac",
    # Game assets (usually pre-compressed)
    ".pak", ".vpk", ".wem", ".bnk", ".bk2", ".usm",
    ".uasset", ".uexp",  # Unreal — sometimes compressed, sometimes not
    # Documents (zip containers)
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".jar", ".apk",
    # Disk images
    ".iso", ".dmg", ".vmdk", ".qcow2",
}

# ─── Entropy thresholds ──────────────────────────────────────────────────────
ENTROPY_HIGH = 7.2    # Almost certainly compressed/encrypted — skip
ENTROPY_LOW = 6.0     # Likely compressible — compress aggressively
ENTROPY_SAMPLE_SIZE = 4096  # Bytes to sample per region


def check_magic_bytes(filepath: str) -> str | None:
    """
    Check the first 16 bytes against known compressed format signatures.
    Returns format name if matched, None otherwise.
    O(1) — reads only 16 bytes.
    """
    try:
        with open(filepath, "rb") as f:
            header = f.read(16)
        if len(header) < 2:
            return None
        for sig, fmt in MAGIC_SIGNATURES:
            if header[:len(sig)] == sig:
                return fmt
    except OSError:
        pass
    return None


def check_extension(filepath: str) -> bool:
    """Check if file extension indicates already-compressed format."""
    ext = Path(filepath).suffix.lower()
    return ext in COMPRESSED_EXTENSIONS


def compute_shannon_entropy(data: bytes) -> float:
    """
    Compute Shannon entropy for a byte sequence.
    Returns value 0.0 (uniform) to 8.0 (random/compressed/encrypted).
    Optimized with pre-allocated frequency array.
    """
    if not data:
        return 0.0

    frequency = [0] * 256
    for byte in data:
        frequency[byte] += 1

    data_len = len(data)
    entropy = 0.0
    inv_len = 1.0 / data_len

    for count in frequency:
        if count > 0:
            p = count * inv_len
            entropy -= p * math.log2(p)

    return entropy


def sample_entropy(filepath: str, sample_size: int = ENTROPY_SAMPLE_SIZE) -> float:
    """
    Fast entropy estimation by sampling head + middle of file.
    Does NOT read the entire file — O(sample_size) regardless of file size.
    """
    try:
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return 0.0

        sample_data = bytearray()

        with open(filepath, "rb") as f:
            if file_size <= sample_size * 3:
                # Small file — just read all of it
                sample_data = bytearray(f.read())
            else:
                # Sample head
                sample_data.extend(f.read(sample_size))
                # Sample middle
                f.seek(file_size // 2)
                sample_data.extend(f.read(sample_size))
                # Sample near end (but not the very last bytes which may be padding)
                f.seek(max(0, file_size - sample_size - 1024))
                sample_data.extend(f.read(sample_size))

        return compute_shannon_entropy(bytes(sample_data))
    except OSError:
        return 0.0


class CompressionDecision:
    """Result of analyzing whether a file should be compressed."""

    __slots__ = ("should_compress", "reason", "format_detected", "entropy", "level")

    def __init__(
        self,
        should_compress: bool,
        reason: str,
        format_detected: str = "",
        entropy: float = 0.0,
        level: int = 3,
    ):
        self.should_compress = should_compress
        self.reason = reason
        self.format_detected = format_detected
        self.entropy = entropy
        self.level = level  # zstd compression level to use

    def __repr__(self) -> str:
        status = "COMPRESS" if self.should_compress else "SKIP"
        return f"<{status}: {self.reason} (entropy={self.entropy:.2f}, fmt={self.format_detected})>"


def analyze_file(filepath: str) -> CompressionDecision:
    """
    Full analysis pipeline for a single file:
    1. Magic bytes check (O(1) — 16 bytes)
    2. Extension check (O(1) — string match)
    3. Entropy sampling (O(sample_size) — ~12KB read)

    Returns CompressionDecision with should_compress, reason, and recommended level.
    """
    # Phase 1: Magic bytes — instant known-format detection
    fmt = check_magic_bytes(filepath)
    if fmt:
        # Some formats are mixed — exe/dll and wav could be uncompressed
        if fmt in ("exe_dll", "elf", "wav_avi"):
            # Fall through to entropy check
            pass
        else:
            return CompressionDecision(
                should_compress=False,
                reason=f"Known compressed format: {fmt}",
                format_detected=fmt,
                entropy=8.0,  # assumed high
            )

    # Phase 2: Extension check — catch formats without magic bytes
    if check_extension(filepath):
        return CompressionDecision(
            should_compress=False,
            reason=f"Compressed file extension: {Path(filepath).suffix}",
            format_detected=Path(filepath).suffix,
            entropy=7.5,  # assumed high
        )

    # Phase 3: Entropy sampling — catch everything else
    entropy = sample_entropy(filepath)

    if entropy > ENTROPY_HIGH:
        return CompressionDecision(
            should_compress=False,
            reason=f"High entropy ({entropy:.2f}) — likely already compressed",
            entropy=entropy,
        )

    if entropy < ENTROPY_LOW:
        return CompressionDecision(
            should_compress=True,
            reason=f"Low entropy ({entropy:.2f}) — highly compressible",
            entropy=entropy,
            level=3,  # balanced zstd
        )

    # Medium entropy — compress with fast level, worth trying
    return CompressionDecision(
        should_compress=True,
        reason=f"Medium entropy ({entropy:.2f}) — may compress slightly",
        entropy=entropy,
        level=1,  # fast zstd — bail quickly if no gain
    )
