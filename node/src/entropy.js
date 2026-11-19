/**
 * cloud-boost v3 — entropy detection module
 * Fast compressed format detection via magic bytes + Shannon entropy sampling.
 * Decides whether compression will help BEFORE wasting CPU on it.
 */

import fs from 'fs';
import path from 'path';

// ─── Magic Byte Signatures (known compressed/encrypted formats) ──────────────
// Format: { bytes: Buffer, name: string }
const MAGIC_SIGNATURES = [
  // Archives (already compressed internally)
  { bytes: Buffer.from([0x50, 0x4b, 0x03, 0x04]), name: 'zip' },
  { bytes: Buffer.from([0x50, 0x4b, 0x05, 0x06]), name: 'zip_empty' },
  { bytes: Buffer.from([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07]), name: 'rar' },
  { bytes: Buffer.from([0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]), name: '7z' },
  { bytes: Buffer.from([0x1f, 0x8b, 0x08]), name: 'gzip' },
  { bytes: Buffer.from([0x42, 0x5a, 0x68]), name: 'bzip2' },
  { bytes: Buffer.from([0x28, 0xb5, 0x2f, 0xfd]), name: 'zstd' },
  { bytes: Buffer.from([0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00]), name: 'xz' },
  { bytes: Buffer.from([0x04, 0x22, 0x4d, 0x18]), name: 'lz4' },
  // Media (already compressed)
  { bytes: Buffer.from([0xff, 0xd8, 0xff]), name: 'jpeg' },
  { bytes: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), name: 'png' },
  { bytes: Buffer.from([0x47, 0x49, 0x46, 0x38, 0x37, 0x61]), name: 'gif' },
  { bytes: Buffer.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]), name: 'gif' },
  { bytes: Buffer.from([0x1a, 0x45, 0xdf, 0xa3]), name: 'webm_mkv' },
  { bytes: Buffer.from([0x00, 0x00, 0x00, 0x1c, 0x66, 0x74, 0x79, 0x70]), name: 'mp4' },
  { bytes: Buffer.from([0x00, 0x00, 0x00, 0x20, 0x66, 0x74, 0x79, 0x70]), name: 'mp4' },
  { bytes: Buffer.from([0x49, 0x44, 0x33]), name: 'mp3' },
  { bytes: Buffer.from([0xff, 0xfb]), name: 'mp3' },
  { bytes: Buffer.from([0xff, 0xf3]), name: 'mp3' },
  { bytes: Buffer.from([0x4f, 0x67, 0x67, 0x53]), name: 'ogg' },
  { bytes: Buffer.from([0x66, 0x4c, 0x61, 0x43]), name: 'flac' },
  { bytes: Buffer.from([0x52, 0x49, 0x46, 0x46]), name: 'wav_avi' },
  // Game-specific formats (typically pre-compressed)
  { bytes: Buffer.from([0xc1, 0x83, 0x2a, 0x9e]), name: 'unreal_pak' },
  // Executables (mixed — entropy will decide)
  { bytes: Buffer.from([0x4d, 0x5a]), name: 'exe_dll' },
  { bytes: Buffer.from([0x7f, 0x45, 0x4c, 0x46]), name: 'elf' },
];

// File extensions known to be already compressed (fallback if no magic match)
const COMPRESSED_EXTENSIONS = new Set([
  // Archives
  '.zip', '.gz', '.gzip', '.bz2', '.xz', '.lz', '.lz4', '.zst', '.zstd',
  '.7z', '.rar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz',
  '.cab', '.lzma', '.lzo',
  // Media — images
  '.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.heic',
  // Media — video
  '.mp4', '.mkv', '.avi', '.webm', '.mov', '.wmv', '.flv',
  // Media — audio
  '.mp3', '.aac', '.ogg', '.opus', '.wma', '.m4a', '.flac',
  // Game assets (usually pre-compressed)
  '.pak', '.vpk', '.wem', '.bnk', '.bk2', '.usm',
  '.uasset', '.uexp',
  // Documents (zip containers)
  '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.jar', '.apk',
  // Disk images
  '.iso', '.dmg', '.vmdk', '.qcow2',
]);

// ─── Entropy thresholds ──────────────────────────────────────────────────────
const ENTROPY_HIGH = 7.2;
const ENTROPY_LOW = 6.0;
const ENTROPY_SAMPLE_SIZE = 4096;

/**
 * Check the first 16 bytes against known compressed format signatures.
 * Returns format name if matched, null otherwise. O(1) — reads only 16 bytes.
 */
export function checkMagicBytes(filepath) {
  try {
    const fd = fs.openSync(filepath, 'r');
    const header = Buffer.alloc(16);
    const bytesRead = fs.readSync(fd, header, 0, 16, 0);
    fs.closeSync(fd);

    if (bytesRead < 2) return null;

    for (const sig of MAGIC_SIGNATURES) {
      if (bytesRead >= sig.bytes.length) {
        const slice = header.subarray(0, sig.bytes.length);
        if (slice.equals(sig.bytes)) {
          return sig.name;
        }
      }
    }
  } catch {
    // File read error — skip
  }
  return null;
}

/**
 * Check if file extension indicates already-compressed format.
 */
export function checkExtension(filepath) {
  const ext = path.extname(filepath).toLowerCase();
  return COMPRESSED_EXTENSIONS.has(ext);
}

/**
 * Compute Shannon entropy for a byte buffer.
 * Returns value 0.0 (uniform) to 8.0 (random/compressed/encrypted).
 */
export function computeShannonEntropy(data) {
  if (!data || data.length === 0) return 0.0;

  const frequency = new Uint32Array(256);
  for (let i = 0; i < data.length; i++) {
    frequency[data[i]]++;
  }

  const dataLen = data.length;
  const invLen = 1.0 / dataLen;
  let entropy = 0.0;

  for (let i = 0; i < 256; i++) {
    if (frequency[i] > 0) {
      const p = frequency[i] * invLen;
      entropy -= p * Math.log2(p);
    }
  }

  return entropy;
}

/**
 * Fast entropy estimation by sampling head + middle + near-end of file.
 * Does NOT read the entire file — O(sampleSize) regardless of file size.
 */
export function sampleEntropy(filepath, sampleSize = ENTROPY_SAMPLE_SIZE) {
  try {
    const stat = fs.statSync(filepath);
    const fileSize = stat.size;
    if (fileSize === 0) return 0.0;

    const fd = fs.openSync(filepath, 'r');
    let sampleData;

    if (fileSize <= sampleSize * 3) {
      // Small file — read all of it
      sampleData = Buffer.alloc(fileSize);
      fs.readSync(fd, sampleData, 0, fileSize, 0);
    } else {
      const parts = [];

      // Sample head
      const head = Buffer.alloc(sampleSize);
      fs.readSync(fd, head, 0, sampleSize, 0);
      parts.push(head);

      // Sample middle
      const mid = Buffer.alloc(sampleSize);
      fs.readSync(fd, mid, 0, sampleSize, Math.floor(fileSize / 2));
      parts.push(mid);

      // Sample near end (not the very last bytes which may be padding)
      const endOffset = Math.max(0, fileSize - sampleSize - 1024);
      const tail = Buffer.alloc(sampleSize);
      fs.readSync(fd, tail, 0, sampleSize, endOffset);
      parts.push(tail);

      sampleData = Buffer.concat(parts);
    }

    fs.closeSync(fd);
    return computeShannonEntropy(sampleData);
  } catch {
    return 0.0;
  }
}

/**
 * Result of analyzing whether a file should be compressed.
 */
export class CompressionDecision {
  constructor({
    shouldCompress = false,
    reason = '',
    formatDetected = '',
    entropy = 0.0,
    level = 3,
  } = {}) {
    this.shouldCompress = shouldCompress;
    this.reason = reason;
    this.formatDetected = formatDetected;
    this.entropy = entropy;
    this.level = level;
  }

  toString() {
    const status = this.shouldCompress ? 'COMPRESS' : 'SKIP';
    return `<${status}: ${this.reason} (entropy=${this.entropy.toFixed(2)}, fmt=${this.formatDetected})>`;
  }
}

/**
 * Full analysis pipeline for a single file:
 * 1. Magic bytes check (O(1) — 16 bytes)
 * 2. Extension check (O(1) — string match)
 * 3. Entropy sampling (O(sampleSize) — ~12KB read)
 *
 * Returns CompressionDecision with shouldCompress, reason, and recommended level.
 */
export function analyzeFile(filepath) {
  // Phase 1: Magic bytes — instant known-format detection
  const fmt = checkMagicBytes(filepath);
  if (fmt) {
    // Some formats are mixed — exe/dll and wav could be uncompressed
    if (fmt !== 'exe_dll' && fmt !== 'elf' && fmt !== 'wav_avi') {
      return new CompressionDecision({
        shouldCompress: false,
        reason: `Known compressed format: ${fmt}`,
        formatDetected: fmt,
        entropy: 8.0,
      });
    }
    // Fall through to entropy check for mixed formats
  }

  // Phase 2: Extension check — catch formats without magic bytes
  if (checkExtension(filepath)) {
    const ext = path.extname(filepath).toLowerCase();
    return new CompressionDecision({
      shouldCompress: false,
      reason: `Compressed file extension: ${ext}`,
      formatDetected: ext,
      entropy: 7.5,
    });
  }

  // Phase 3: Entropy sampling — catch everything else
  const entropy = sampleEntropy(filepath);

  if (entropy > ENTROPY_HIGH) {
    return new CompressionDecision({
      shouldCompress: false,
      reason: `High entropy (${entropy.toFixed(2)}) — likely already compressed`,
      entropy,
    });
  }

  if (entropy < ENTROPY_LOW) {
    return new CompressionDecision({
      shouldCompress: true,
      reason: `Low entropy (${entropy.toFixed(2)}) — highly compressible`,
      entropy,
      level: 3,
    });
  }

  // Medium entropy — compress with fast level, worth trying
  return new CompressionDecision({
    shouldCompress: true,
    reason: `Medium entropy (${entropy.toFixed(2)}) — may compress slightly`,
    entropy,
    level: 1,
  });
}
