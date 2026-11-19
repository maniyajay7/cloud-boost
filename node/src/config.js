/**
 * cloud-boost v3 — configuration manager
 * Handles: loading, validation, defaults, user overrides, auto-tune engine
 */

import fs from 'fs';
import path from 'path';
import os from 'os';

// ─── Size Constants ──────────────────────────────────────────────────────────
export const KB = 1024;
export const MB = 1024 * KB;
export const GB = 1024 * MB;

// ─── Directories ─────────────────────────────────────────────────────────────
export const CONFIG_DIR = path.join(os.homedir(), '.cloudboost');
export const CHECKPOINTS_DIR = path.join(CONFIG_DIR, 'checkpoints');
export const LOGS_DIR = path.join(CONFIG_DIR, 'logs');
export const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json');

// ─── Supported Providers ─────────────────────────────────────────────────────
export const PROVIDERS = ['gdrive', 's3', 'azure', 'dropbox', 'spaces', 'supabase'];

/**
 * Parse human-readable size string to bytes. e.g. '5MB' → 5242880
 */
export function parseSize(sizeStr) {
  const str = sizeStr.trim().toUpperCase();
  const multipliers = { GB, MB, KB, B: 1 };
  for (const [suffix, mult] of Object.entries(multipliers)) {
    if (str.endsWith(suffix)) {
      return Math.floor(parseFloat(str.slice(0, -suffix.length).trim()) * mult);
    }
  }
  return parseInt(str, 10);
}

/**
 * Format bytes to human-readable string.
 */
export function formatSize(sizeBytes) {
  if (sizeBytes >= GB) return `${(sizeBytes / GB).toFixed(2)} GB`;
  if (sizeBytes >= MB) return `${(sizeBytes / MB).toFixed(2)} MB`;
  if (sizeBytes >= KB) return `${(sizeBytes / KB).toFixed(2)} KB`;
  return `${sizeBytes} B`;
}

/**
 * Format seconds to human-readable duration.
 */
export function formatDuration(seconds) {
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

/**
 * Upload configuration with validated defaults.
 */
export class UploadConfig {
  constructor(opts = {}) {
    this.threads = opts.threads ?? 6;
    this.chunkSize = opts.chunkSize ?? 8 * MB;
    this.compression = opts.compression ?? true;
    this.compressionLevel = opts.compressionLevel ?? 6;
    this.resume = opts.resume ?? true;
    this.provider = opts.provider ?? 'gdrive';
    this.maxBandwidth = opts.maxBandwidth ?? 0;
    this.maxRetries = opts.maxRetries ?? 3;
    this.retryDelay = opts.retryDelay ?? 2.0;
    this.checksumVerify = opts.checksumVerify ?? true;
    this.rcloneTransfers = opts.rcloneTransfers ?? 8;
    this.rcloneBufferSize = opts.rcloneBufferSize ?? '256M';
    this.rcloneDriveChunk = opts.rcloneDriveChunk ?? '64M';
    this.dedup = opts.dedup ?? true;
    this.tpsLimit = opts.tpsLimit ?? 10;
    this.maxTransfer = opts.maxTransfer ?? 0;
    this.logFile = opts.logFile ?? '';
    this.verbose = opts.verbose ?? false;

    this._validate();
  }

  _validate() {
    if (this.threads < 1 || this.threads > 32) {
      throw new Error(`Threads must be 1-32, got ${this.threads}`);
    }
    if (this.chunkSize < 1 * MB) {
      throw new Error(`Chunk size must be >= 1MB, got ${formatSize(this.chunkSize)}`);
    }
    if (this.chunkSize > 512 * MB) {
      throw new Error(`Chunk size must be <= 512MB, got ${formatSize(this.chunkSize)}`);
    }
    if (this.compressionLevel < 1 || this.compressionLevel > 9) {
      throw new Error(`Compression level must be 1-9, got ${this.compressionLevel}`);
    }
    if (this.maxRetries < 0 || this.maxRetries > 10) {
      throw new Error(`Max retries must be 0-10, got ${this.maxRetries}`);
    }
    if (!PROVIDERS.includes(this.provider)) {
      throw new Error(`Provider must be one of [${PROVIDERS.join(', ')}], got ${this.provider}`);
    }
  }

  /**
   * Auto-tune configuration based on file size.
   * Small (<100MB), Medium (<1GB), Large (<10GB), Huge (10GB+)
   */
  static autoTune(fileSize, overrides = {}) {
    const opts = {};

    if (fileSize < 100 * MB) {
      Object.assign(opts, {
        threads: 4,
        chunkSize: 4 * MB,
        compressionLevel: 6,
        rcloneTransfers: 4,
        rcloneDriveChunk: '64M',
      });
    } else if (fileSize < 1 * GB) {
      Object.assign(opts, {
        threads: 6,
        chunkSize: 8 * MB,
        compressionLevel: 6,
        rcloneTransfers: 6,
        rcloneDriveChunk: '128M',
      });
    } else if (fileSize < 10 * GB) {
      Object.assign(opts, {
        threads: 8,
        chunkSize: 16 * MB,
        compressionLevel: 4,
        rcloneTransfers: 8,
        rcloneDriveChunk: '128M',
      });
    } else {
      Object.assign(opts, {
        threads: 12,
        chunkSize: 32 * MB,
        compressionLevel: 2,
        rcloneTransfers: 8,
        rcloneDriveChunk: '256M',
        tpsLimit: 10,
      });
    }

    // User overrides always win
    for (const [key, val] of Object.entries(overrides)) {
      if (val !== undefined && val !== null) {
        opts[key] = val;
      }
    }

    return new UploadConfig(opts);
  }
}

/**
 * Create required directories if they don't exist.
 */
export function ensureDirs() {
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
  fs.mkdirSync(CHECKPOINTS_DIR, { recursive: true });
  fs.mkdirSync(LOGS_DIR, { recursive: true });
}

/**
 * Load user config from ~/.cloudboost/config.json
 */
export function loadConfig() {
  ensureDirs();
  if (fs.existsSync(CONFIG_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
    } catch {
      return {};
    }
  }
  return {};
}

/**
 * Write default config file if it doesn't exist.
 */
export function saveDefaultConfig() {
  ensureDirs();
  if (!fs.existsSync(CONFIG_FILE)) {
    const defaults = {
      threads: 6,
      chunkSize: '8MB',
      compression: true,
      compressionLevel: 6,
      resume: true,
      provider: 'gdrive',
      maxBandwidth: 0,
      maxRetries: 3,
      checksumVerify: true,
      dedup: true,
      verbose: false,
    };
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(defaults, null, 2));
  }
}
