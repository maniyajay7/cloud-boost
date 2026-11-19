/**
 * cloud-boost v3 — compressor module
 * Streaming compression with gzip (Node builtin) and optional zstd.
 * Automatically selects algorithm and level based on entropy analysis.
 */

import fs from 'fs';
import path from 'path';
import zlib from 'zlib';
import { pipeline } from 'stream/promises';
import { analyzeFile } from './entropy.js';

// ─── zstd availability check ─────────────────────────────────────────────────
let zstdModule = null;
let HAS_ZSTD = false;

try {
  zstdModule = await import('@aspect-build/zstd');
  HAS_ZSTD = true;
} catch {
  try {
    zstdModule = await import('simple-zstd');
    HAS_ZSTD = true;
  } catch {
    HAS_ZSTD = false;
  }
}

// ─── Constants ───────────────────────────────────────────────────────────────
const STREAM_BUFFER = 1024 * 1024; // 1MB read buffer

/**
 * Compress a file using gzip (Node builtin).
 * Returns compressed size in bytes.
 */
export async function compressFileGzip(srcPath, dstPath, level = 6) {
  const src = fs.createReadStream(srcPath, { highWaterMark: STREAM_BUFFER });
  const dst = fs.createWriteStream(dstPath);
  const gzip = zlib.createGzip({ level });

  await pipeline(src, gzip, dst);
  return fs.statSync(dstPath).size;
}

/**
 * Compress a file using zstd (if available).
 * Returns compressed size in bytes.
 */
export async function compressFileZstd(srcPath, dstPath, level = 3) {
  if (!HAS_ZSTD || !zstdModule) {
    throw new Error('zstd not available — use compressFileGzip instead');
  }

  // Implementation depends on which zstd package is available
  if (typeof zstdModule.compress === 'function') {
    // @aspect-build/zstd style — compress buffer
    const input = fs.readFileSync(srcPath);
    const compressed = await zstdModule.compress(input, level);
    fs.writeFileSync(dstPath, compressed);
    return compressed.length;
  }

  if (typeof zstdModule.ZstdCompressTransform === 'function') {
    // simple-zstd style — streaming
    const src = fs.createReadStream(srcPath, { highWaterMark: STREAM_BUFFER });
    const dst = fs.createWriteStream(dstPath);
    const compressor = new zstdModule.ZstdCompressTransform({ level });
    await pipeline(src, compressor, dst);
    return fs.statSync(dstPath).size;
  }

  throw new Error('zstd module loaded but no compatible compress API found');
}

/**
 * Smart file compression pipeline.
 * 1. Analyze file entropy if no decision provided
 * 2. Skip if already compressed
 * 3. Compress with zstd (or gzip fallback) if beneficial
 * 4. Verify compression actually reduced size (5% threshold)
 *
 * Returns: { outputPath, outputSize, wasCompressed }
 */
export async function compressFile(srcPath, dstPath = null, decision = null) {
  const originalSize = fs.statSync(srcPath).size;

  // Analyze if no decision provided
  if (decision === null) {
    decision = analyzeFile(srcPath);
  }

  // Skip compression if analysis says so
  if (!decision.shouldCompress) {
    return { outputPath: srcPath, outputSize: originalSize, wasCompressed: false };
  }

  // Determine output path
  if (dstPath === null) {
    const ext = HAS_ZSTD ? '.zst' : '.gz';
    dstPath = srcPath + ext;
  }

  // Compress
  let compressedSize;
  const level = decision.level;

  if (HAS_ZSTD) {
    try {
      compressedSize = await compressFileZstd(srcPath, dstPath, level);
    } catch {
      // Fall back to gzip if zstd fails
      const gzipLevel = Math.min(9, Math.max(1, level + 3));
      compressedSize = await compressFileGzip(srcPath, dstPath, gzipLevel);
    }
  } else {
    const gzipLevel = Math.min(9, Math.max(1, level + 3));
    compressedSize = await compressFileGzip(srcPath, dstPath, gzipLevel);
  }

  // Verify compression actually helped (must save at least 5%)
  const ratio = originalSize > 0 ? compressedSize / originalSize : 1.0;
  if (ratio > 0.95) {
    // Compression didn't help — delete compressed file, use original
    try { fs.unlinkSync(dstPath); } catch { /* ignore */ }
    return { outputPath: srcPath, outputSize: originalSize, wasCompressed: false };
  }

  return { outputPath: dstPath, outputSize: compressedSize, wasCompressed: true };
}

/**
 * Return the file extension for the active compressor.
 */
export function getCompressionExt() {
  return HAS_ZSTD ? '.zst' : '.gz';
}

/**
 * Return the name of the active compressor.
 */
export function getCompressorName() {
  return HAS_ZSTD ? 'zstd' : 'gzip';
}
