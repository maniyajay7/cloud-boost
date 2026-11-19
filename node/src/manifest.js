/**
 * cloud-boost v3 — manifest module
 * File-level upload tracking with integrity verification.
 * Replaces the per-chunk checkpoint system with smarter file-level manifests.
 */

import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { CHECKPOINTS_DIR, ensureDirs } from './config.js';

const MANIFEST_VERSION = 3;

/**
 * Generate a unique key for a file based on absolute path.
 */
function _manifestKey(filepath) {
  const absPath = path.resolve(filepath);
  return crypto.createHash('md5').update(absPath).digest('hex').slice(0, 16);
}

/**
 * Get manifest path for a given source file.
 */
export function getManifestPath(filepath) {
  ensureDirs();
  const key = _manifestKey(filepath);
  const stem = path.basename(filepath, path.extname(filepath)).slice(0, 32);
  return path.join(CHECKPOINTS_DIR, `${stem}_${key}.manifest.json`);
}

/**
 * Create a fresh manifest for a file.
 */
export function createManifest(filepath) {
  const absPath = path.resolve(filepath);
  const stat = fs.statSync(absPath);
  const now = new Date().toISOString();

  return {
    version: MANIFEST_VERSION,
    source_file: absPath,
    source_name: path.basename(filepath),
    source_size: stat.size,
    created_at: now,
    updated_at: now,
    status: 'pending',
    compressed: false,
    compressed_path: '',
    compressed_size: 0,
    compression_ratio: 0.0,
    compressor: '',
    destination: '',
    upload_started_at: '',
    upload_completed_at: '',
    bytes_uploaded: 0,
    error: '',
  };
}

/**
 * Load existing manifest for a file. Returns null if not found.
 */
export function loadManifest(filepath) {
  const manifestPath = getManifestPath(filepath);
  if (fs.existsSync(manifestPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
      if (data.version === MANIFEST_VERSION) {
        return data;
      }
    } catch {
      // Invalid JSON or missing fields
    }
  }
  return null;
}

/**
 * Save manifest to disk.
 */
export function saveManifest(filepath, manifest) {
  manifest.updated_at = new Date().toISOString();
  const manifestPath = getManifestPath(filepath);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
}

/**
 * Mark file as currently uploading.
 */
export function markUploading(filepath, manifest, destination) {
  manifest.status = 'uploading';
  manifest.destination = destination;
  manifest.upload_started_at = new Date().toISOString();
  saveManifest(filepath, manifest);
}

/**
 * Mark file as successfully uploaded.
 */
export function markCompleted(filepath, manifest) {
  manifest.status = 'completed';
  manifest.upload_completed_at = new Date().toISOString();
  manifest.bytes_uploaded = manifest.compressed_size || manifest.source_size || 0;
  saveManifest(filepath, manifest);
}

/**
 * Mark file as failed with error message.
 */
export function markFailed(filepath, manifest, error) {
  manifest.status = 'failed';
  manifest.error = (error || '').slice(0, 500);
  saveManifest(filepath, manifest);
}

/**
 * Record compression result in manifest.
 */
export function markCompressed(filepath, manifest, compressedPath, compressedSize, compressor) {
  const originalSize = manifest.source_size || 0;
  manifest.compressed = true;
  manifest.compressed_path = compressedPath;
  manifest.compressed_size = compressedSize;
  manifest.compressor = compressor;
  manifest.compression_ratio = originalSize > 0
    ? Math.round((1 - compressedSize / originalSize) * 1000) / 10
    : 0;
  saveManifest(filepath, manifest);
}

/**
 * Remove manifest after successful upload.
 */
export function clearManifest(filepath) {
  const manifestPath = getManifestPath(filepath);
  try {
    fs.unlinkSync(manifestPath);
  } catch {
    // Already removed or doesn't exist
  }
}

/**
 * Check if a file was already successfully uploaded to the same destination.
 */
export function isAlreadyUploaded(filepath, destination) {
  const manifest = loadManifest(filepath);
  if (!manifest) return false;

  try {
    const currentSize = fs.statSync(filepath).size;
    return (
      manifest.status === 'completed' &&
      manifest.destination === destination &&
      manifest.source_size === currentSize
    );
  } catch {
    return false;
  }
}

/**
 * List all manifests that are in 'uploading' or 'failed' state (resumable).
 */
export function listPendingUploads() {
  ensureDirs();
  const pending = [];

  try {
    const files = fs.readdirSync(CHECKPOINTS_DIR);
    for (const file of files) {
      if (!file.endsWith('.manifest.json')) continue;
      try {
        const fullPath = path.join(CHECKPOINTS_DIR, file);
        const data = JSON.parse(fs.readFileSync(fullPath, 'utf8'));
        if (data.status === 'uploading' || data.status === 'failed') {
          pending.push(data);
        }
      } catch {
        continue;
      }
    }
  } catch {
    // Directory read error
  }

  return pending;
}
