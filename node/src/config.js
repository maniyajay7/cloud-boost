/**
 * cloud-boost config (Node.js)
 * Handles: auto-tuning, validation, defaults
 */

'use strict';

const os = require('os');
const path = require('path');
const fs = require('fs-extra');

const KB = 1024;
const MB = 1024 * KB;
const GB = 1024 * MB;

const CONFIG_DIR = path.join(os.homedir(), '.cloudboost');
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.yml');
const CHECKPOINTS_DIR = path.join(CONFIG_DIR, 'checkpoints');
const LOGS_DIR = path.join(CONFIG_DIR, 'logs');

const PROVIDERS = ['gdrive', 's3', 'azure', 'dropbox', 'spaces', 'supabase'];

function parseSize(sizeStr) {
  const str = sizeStr.toString().trim().toUpperCase();
  const multipliers = { GB, MB, KB, B: 1 };
  for (const [suffix, mult] of Object.entries(multipliers)) {
    if (str.endsWith(suffix)) {
      return Math.floor(parseFloat(str.slice(0, -suffix.length)) * mult);
    }
  }
  return parseInt(str);
}

function formatSize(bytes) {
  if (bytes >= GB) return `${(bytes / GB).toFixed(2)} GB`;
  if (bytes >= MB) return `${(bytes / MB).toFixed(2)} MB`;
  if (bytes >= KB) return `${(bytes / KB).toFixed(2)} KB`;
  return `${bytes} B`;
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
  }
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${mins}m`;
}

function defaultConfig() {
  return {
    threads: 6,
    chunkSize: 8 * MB,
    compression: true,
    compressionLevel: 6,
    resume: true,
    provider: 'gdrive',
    maxBandwidth: 0,
    maxRetries: 3,
    retryDelay: 2.0,
    checksumVerify: true,
    rcloneTransfers: 8,
    rcloneBufferSize: '256M',
    rcloneDriveChunk: '64M',
    dedup: true,
    verbose: false,
  };
}

function autoTune(fileSize, overrides = {}) {
  const config = defaultConfig();

  if (fileSize < 100 * MB) {
    config.threads = 4;
    config.chunkSize = 4 * MB;
    config.compressionLevel = 6;
  } else if (fileSize < 1 * GB) {
    config.threads = 6;
    config.chunkSize = 8 * MB;
    config.compressionLevel = 6;
  } else if (fileSize < 10 * GB) {
    config.threads = 8;
    config.chunkSize = 16 * MB;
    config.compressionLevel = 4;
    config.rcloneTransfers = 12;
  } else {
    config.threads = 12;
    config.chunkSize = 32 * MB;
    config.compressionLevel = 2;
    config.rcloneTransfers = 16;
    config.rcloneDriveChunk = '128M';
  }

  // User overrides always win
  for (const [key, val] of Object.entries(overrides)) {
    if (val !== undefined && val !== null && key in config) {
      config[key] = val;
    }
  }

  return config;
}

async function ensureDirs() {
  await fs.ensureDir(CONFIG_DIR);
  await fs.ensureDir(CHECKPOINTS_DIR);
  await fs.ensureDir(LOGS_DIR);
}

module.exports = {
  KB, MB, GB,
  CONFIG_DIR, CONFIG_FILE, CHECKPOINTS_DIR, LOGS_DIR,
  PROVIDERS,
  parseSize, formatSize, formatDuration,
  defaultConfig, autoTune, ensureDirs,
};
