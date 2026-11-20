/**
 * cloud-boost v3 — transport layer (REWRITTEN)
 * Single rclone invocation per file/batch. No per-chunk subprocess spawning.
 * Handles: optimized rclone flags, quota tracking, rate limit detection.
 */

import { execSync, spawn } from 'child_process';
import path from 'path';
import fs from 'fs';
import { formatSize } from './config.js';

// ─── Quota Tracker ───────────────────────────────────────────────────────────
const GB = 1024 * 1024 * 1024;

export class QuotaTracker {
  constructor(provider = 'gdrive') {
    this.provider = provider;
    this.dailyLimit = 750 * GB; // 750 GB
    this.sessionBytes = 0;
    this.warnThreshold = 0.93; // Warn at 93% (697 GB)
  }

  add(bytesUploaded) {
    this.sessionBytes += bytesUploaded;
  }

  /** Returns: 'ok', 'warning', or 'blocked'. */
  check() {
    if (this.provider !== 'gdrive') return 'ok';
    if (this.sessionBytes >= this.dailyLimit) return 'blocked';
    if (this.sessionBytes >= Math.floor(this.dailyLimit * this.warnThreshold)) return 'warning';
    return 'ok';
  }

  remaining() {
    return Math.max(0, this.dailyLimit - this.sessionBytes);
  }

  usagePct() {
    return this.dailyLimit > 0 ? (this.sessionBytes / this.dailyLimit) * 100 : 0;
  }
}

// ─── rclone checks ───────────────────────────────────────────────────────────

/**
 * Verify rclone is installed and accessible.
 */
export function checkRcloneInstalled() {
  try {
    execSync('rclone version', { stdio: 'pipe', timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

/**
 * Get rclone version string.
 */
export function getRcloneVersion() {
  try {
    const result = execSync('rclone version --check', { encoding: 'utf8', timeout: 10000 });
    return result.trim().split('\n')[0] || 'unknown';
  } catch {
    return 'unknown';
  }
}

/**
 * List configured rclone remotes.
 */
export function getRcloneRemotes() {
  try {
    const result = execSync('rclone listremotes', { encoding: 'utf8', timeout: 10000 });
    return result.trim().split('\n').filter(r => r.trim()).map(r => r.trim());
  } catch {
    return [];
  }
}

/**
 * Quick check if a remote is accessible (token not expired, etc).
 */
export function checkRemoteAccessible(remote) {
  try {
    execSync(`rclone about ${remote} --json`, { encoding: 'utf8', timeout: 30000 });
    return true;
  } catch {
    return false;
  }
}

// ─── Build optimized rclone command ──────────────────────────────────────────

/**
 * Build optimized rclone flags based on config and provider.
 */
export function buildRcloneFlags(config) {
  const flags = [
    `--transfers=${config.rcloneTransfers}`,
    `--buffer-size=${config.rcloneBufferSize}`,
    '--checkers=16',
    '--contimeout=60s',
    '--timeout=600s',
    '--retries=5',
    '--retries-sleep=3s',
    '--low-level-retries=10',
    '--stats=1s',
    '--stats-one-line',
    `--tpslimit=${config.tpsLimit}`,
    '--fast-list',
  ];

  // Provider-specific flags
  if (config.provider === 'gdrive') {
    flags.push(
      `--drive-chunk-size=${config.rcloneDriveChunk}`,
      '--drive-upload-cutoff=8M',
      '--drive-use-trash=false',
      '--drive-pacer-min-sleep=10ms',
      '--drive-pacer-burst=200',
    );
  } else if (config.provider === 's3') {
    flags.push(
      '--s3-upload-concurrency=8',
      '--s3-chunk-size=64M',
    );
  }

  // Bandwidth limiting
  if (config.maxBandwidth > 0) {
    const bw = formatSize(config.maxBandwidth).replace(/\s/g, '');
    flags.push(`--bwlimit=${bw}`);
  }

  // Max transfer limit (quota protection)
  if (config.maxTransfer > 0) {
    const mt = formatSize(config.maxTransfer).replace(/\s/g, '');
    flags.push(`--max-transfer=${mt}`);
  }

  // Verbose
  if (config.verbose) {
    flags.push('--verbose');
  }

  return flags;
}

// ─── Upload functions ────────────────────────────────────────────────────────

/**
 * Upload a single file using ONE rclone call with optimized flags.
 * Returns { success: boolean, error: string }
 */
export function uploadFileRclone(source, destination, config, quota = null) {
  // Quota pre-check
  if (quota) {
    const status = quota.check();
    if (status === 'blocked') {
      return { success: false, error: 'Daily upload quota (750GB) reached. Try again tomorrow.' };
    }
    if (status === 'warning') {
      const remaining = formatSize(quota.remaining());
      console.log(`  ⚠ Approaching daily quota — ${remaining} remaining`);
    }
  }

  let fileSize;
  try {
    fileSize = fs.statSync(source).size;
  } catch {
    return { success: false, error: `Cannot stat source file: ${source}` };
  }

  const destPath = `${destination}/${path.basename(source)}`;
  const flags = buildRcloneFlags(config);
  const cmd = ['rclone', 'copyto', source, destPath, ...flags];

  const timeoutMs = Math.max(3600000, Math.floor(fileSize / (1024 * 1024)) * 10000);

  try {
    execSync(cmd.join(' '), { encoding: 'utf8', timeout: timeoutMs, stdio: 'pipe' });
    if (quota) quota.add(fileSize);
    return { success: true, error: '' };
  } catch (err) {
    const stderr = (err.stderr || err.message || '').toLowerCase();
    if (stderr.includes('rate limit') || stderr.includes('403') || stderr.includes('ratelimitexceeded')) {
      return { success: false, error: 'Google Drive rate limit hit. Backing off...' };
    }
    if (stderr.includes('quota')) {
      return { success: false, error: 'Google Drive quota exceeded (750GB/day).' };
    }
    return { success: false, error: (err.stderr || err.message || 'Unknown error').slice(0, 200) };
  }
}

/**
 * Upload an entire folder using ONE rclone call.
 * rclone handles its own parallelism via --transfers.
 * Returns { success: boolean, error: string }
 */
export function uploadFolderRclone(sourceDir, destination, config, quota = null) {
  if (quota) {
    const status = quota.check();
    if (status === 'blocked') {
      return { success: false, error: 'Daily upload quota (750GB) reached.' };
    }
  }

  const flags = buildRcloneFlags(config);
  const cmd = ['rclone', 'copy', sourceDir, destination, ...flags];

  try {
    execSync(cmd.join(' '), { encoding: 'utf8', timeout: 86400000, stdio: 'pipe' }); // 24h max
    return { success: true, error: '' };
  } catch (err) {
    return { success: false, error: (err.stderr || err.message || 'Unknown error').slice(0, 200) };
  }
}

/**
 * Upload with real-time progress tracking via rclone --stats output.
 * Streams stderr for progress updates.
 * Returns Promise<{ success: boolean, error: string }>
 */
export function uploadWithProgress(source, destination, config, quota = null, progressCallback = null) {
  return new Promise((resolve) => {
    const isDir = fs.statSync(source).isDirectory();
    const rcloneCmd = isDir ? 'copy' : 'copyto';
    const dest = isDir ? destination : `${destination}/${path.basename(source)}`;

    const flags = buildRcloneFlags(config);
    const args = [rcloneCmd, source, dest, ...flags];

    const proc = spawn('rclone', args, { stdio: ['ignore', 'pipe', 'pipe'] });

    const lastLines = [];

    proc.stderr.on('data', (chunk) => {
      const lines = chunk.toString().split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed) {
          lastLines.push(trimmed);
          if (lastLines.length > 20) {
            lastLines.shift();
          }
          if (progressCallback) {
            progressCallback(trimmed);
          }
        }
      }
    });

    proc.on('close', (code) => {
      if (code === 0) {
        if (quota) {
          try {
            let totalSize;
            if (isDir) {
              totalSize = 0;
              const walkDir = (dir) => {
                const entries = fs.readdirSync(dir, { withFileTypes: true });
                for (const entry of entries) {
                  const fullPath = path.join(dir, entry.name);
                  if (entry.isFile()) {
                    totalSize += fs.statSync(fullPath).size;
                  } else if (entry.isDirectory()) {
                    walkDir(fullPath);
                  }
                }
              };
              walkDir(source);
            } else {
              totalSize = fs.statSync(source).size;
            }
            quota.add(totalSize);
          } catch {
            // Quota tracking failure is non-fatal
          }
        }
        resolve({ success: true, error: '' });
      } else {
        let errorDetail = 'rclone exited with non-zero status';
        if (lastLines.length > 0) {
          const errorLines = lastLines.filter((l) => l.includes('ERROR') || l.includes('Failed') || l.toLowerCase().includes('fatal'));
          errorDetail = errorLines.length > 0 ? errorLines[errorLines.length - 1].slice(0, 300) : lastLines[lastLines.length - 1].slice(0, 300);
        }
        resolve({ success: false, error: errorDetail });
      }
    });

    proc.on('error', (err) => {
      resolve({ success: false, error: err.message.slice(0, 200) });
    });
  });
}
