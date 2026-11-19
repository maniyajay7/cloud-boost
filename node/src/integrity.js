/**
 * cloud-boost integrity module (Node.js)
 * Handles: SHA-256 checksums, manifests, dedup
 */

'use strict';

const crypto = require('crypto');
const fs = require('fs-extra');
const path = require('path');
const { CHECKPOINTS_DIR } = require('./config');

const HASH_BUFFER = 65536;

async function computeSha256(filepath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = require('fs').createReadStream(filepath, { highWaterMark: HASH_BUFFER });
    stream.on('data', (data) => hash.update(data));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', reject);
  });
}

function computeChunkHash(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function getManifestPath(filepath) {
  const fileHash = crypto.createHash('md5').update(filepath).digest('hex').slice(0, 12);
  const name = path.basename(filepath, path.extname(filepath));
  return path.join(CHECKPOINTS_DIR, `${name}_${fileHash}.manifest.json`);
}

async function loadManifest(filepath) {
  const manifestPath = getManifestPath(filepath);
  if (await fs.pathExists(manifestPath)) {
    return fs.readJSON(manifestPath);
  }
  return {
    source_file: filepath,
    source_hash: '',
    total_chunks: 0,
    chunk_size: 0,
    chunks: {},
    completed: false,
  };
}

async function saveManifest(filepath, manifest) {
  const manifestPath = getManifestPath(filepath);
  await fs.writeJSON(manifestPath, manifest, { spaces: 2 });
}

async function clearManifest(filepath) {
  const manifestPath = getManifestPath(filepath);
  if (await fs.pathExists(manifestPath)) {
    await fs.remove(manifestPath);
  }
}

function isChunkUploaded(manifest, chunkIndex, chunkHash) {
  const key = String(chunkIndex);
  if (manifest.chunks[key]) {
    const entry = manifest.chunks[key];
    return entry.uploaded === true && entry.hash === chunkHash;
  }
  return false;
}

function markChunkUploaded(manifest, chunkIndex, chunkHash, compressedSize) {
  manifest.chunks[String(chunkIndex)] = {
    hash: chunkHash,
    compressed_size: compressedSize,
    uploaded: true,
  };
}

module.exports = {
  computeSha256,
  computeChunkHash,
  getManifestPath,
  loadManifest,
  saveManifest,
  clearManifest,
  isChunkUploaded,
  markChunkUploaded,
};
