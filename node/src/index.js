/**
 * cloud-boost v3 — core uploader (REWRITTEN)
 * Two-tier upload strategy with smart compression and single-rclone-call architecture.
 *
 * Strategy:
 *   < 50MB:   Direct rclone upload (no processing)
 *   50MB+:    Entropy analysis → optional compression → rclone upload with optimized flags
 *   Folder:   Pre-process compressible files → single rclone copy with --transfers
 *   250GB+:   Same as above, rclone handles its own chunking natively
 */

import fs from 'fs';
import path from 'path';
import os from 'os';
import chalk from 'chalk';
import {
  UploadConfig,
  formatSize,
  formatDuration,
  MB,
  GB,
  ensureDirs,
} from './config.js';
import { analyzeFile } from './entropy.js';
import { compressFile, getCompressorName, getCompressionExt } from './compressor.js';
import {
  checkRcloneInstalled,
  getRcloneRemotes,
  uploadFileRclone,
  uploadFolderRclone,
  uploadWithProgress,
  detectRcloneMount,
  serverSideCopy,
  normalizeDestination,
  QuotaTracker,
} from './transport.js';
import {
  createManifest,
  loadManifest,
  saveManifest,
  markUploading,
  markCompleted,
  markFailed,
  markCompressed,
  isAlreadyUploaded,
} from './manifest.js';

const SEPARATOR = '═'.repeat(60);

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const BAR_FILLED = '█';
const BAR_EMPTY = '░';

export class AnimatedProgressBar {
  constructor(totalSize = 0, barLength = 26) {
    this.totalSize = totalSize;
    this.barLength = barLength;
    this.percent = 0.0;
    this.speed = '--';
    this.eta = '--';
    this.xferStr = '';
    this.frameIdx = 0;
  }

  start(label = 'Connecting...') {
    this.render(label);
  }

  updateFromRclone(line) {
    if (!line) return;

    const pctMatch = line.match(/(\d+(?:\.\d+)?)%/);
    if (pctMatch) {
      this.percent = Math.min(100.0, parseFloat(pctMatch[1]));
    }

    const speedMatch = line.match(/([\d.]+\s*[KMGTPE]?i?B\/s)/);
    if (speedMatch) {
      this.speed = speedMatch[1].trim();
    }

    const etaMatch = line.match(/ETA\s+([0-9a-zA-Z]+)/);
    if (etaMatch) {
      this.eta = etaMatch[1].trim();
    } else {
      const tailEta = line.match(/,\s*(\d+[smhd](?:\d+[smhd])*)/);
      if (tailEta) {
        this.eta = tailEta[1].trim();
      }
    }

    const xferMatch = line.match(/([\d.]+\s*[KMGTPE]?i?B)\s*\/\s*([\d.]+\s*[KMGTPE]?i?B)/);
    if (xferMatch) {
      this.xferStr = `${xferMatch[1].trim()}/${xferMatch[2].trim()}`;
    } else if (this.totalSize > 0 && this.percent > 0) {
      const cur = Math.floor(this.totalSize * (this.percent / 100.0));
      this.xferStr = `${formatSize(cur)}/${formatSize(this.totalSize)}`;
    }

    this.render();
  }

  render(label = '') {
    this.frameIdx = (this.frameIdx + 1) % SPINNER_FRAMES.length;
    const spinner = SPINNER_FRAMES[this.frameIdx];

    const filledLen = Math.max(0, Math.min(this.barLength, Math.floor(this.barLength * (this.percent / 100.0))));
    const emptyLen = this.barLength - filledLen;

    const bar = `${chalk.green(BAR_FILLED.repeat(filledLen))}${chalk.gray(BAR_EMPTY.repeat(emptyLen))}`;

    const parts = [
      `\r  ${chalk.cyan(spinner)} [${bar}] ${chalk.yellow(`${this.percent.toFixed(1)}%`)}`
    ];

    if (label) {
      parts.push(chalk.cyan(label));
    } else {
      if (this.xferStr) parts.push(chalk.cyan(this.xferStr));
      if (this.speed !== '--') parts.push(chalk.green(this.speed));
      if (this.eta !== '--') parts.push(`ETA: ${chalk.magenta(this.eta)}`);
    }

    const statusText = parts.join(' | ');
    process.stdout.write(statusText.padEnd(115, ' '));
  }

  complete() {
    const bar = chalk.green(BAR_FILLED.repeat(this.barLength));
    const totalStr = this.totalSize > 0 ? ` | ${chalk.cyan(formatSize(this.totalSize))}` : '';
    process.stdout.write(`\r  ${chalk.green('✓')} [${bar}] ${chalk.green('100.0%')}${totalStr} | ${chalk.green('Done!')}`.padEnd(115, ' ') + '\n');
  }

  fail() {
    const filledLen = Math.max(0, Math.min(this.barLength, Math.floor(this.barLength * (this.percent / 100.0))));
    const emptyLen = this.barLength - filledLen;
    const bar = `${chalk.red(BAR_FILLED.repeat(filledLen))}${chalk.gray(BAR_EMPTY.repeat(emptyLen))}`;
    process.stdout.write(`\r  ${chalk.red('✗')} [${bar}] ${chalk.red(`${this.percent.toFixed(1)}%`)} | ${chalk.red('Interrupted')}`.padEnd(115, ' ') + '\n');
  }
}

/**
 * Recursively collect all files in a directory.
 */
function collectFiles(dir) {
  const results = [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isFile()) {
      results.push(fullPath);
    } else if (entry.isDirectory()) {
      results.push(...collectFiles(fullPath));
    }
  }
  return results.sort();
}

/**
 * Copy a file, creating parent directories as needed.
 */
function copyFile(src, dst) {
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
}

/**
 * Print the upload banner with file info and config.
 */
function printHeader(filepath, destination, config, fileSize) {
  console.log(`\n${chalk.cyan(SEPARATOR)}`);
  console.log(`${chalk.cyan('  ⚡ cloud-boost v3 — Production Upload Engine')}`);
  console.log(`${chalk.cyan(SEPARATOR)}`);
  console.log(`  File       : ${path.basename(filepath)}`);
  console.log(`  Size       : ${formatSize(fileSize)}`);
  console.log(`  Target     : ${destination}`);
  console.log(`  Provider   : ${config.provider}`);
  console.log(`  Compressor : ${getCompressorName()}`);
  console.log(`  rclone     : transfers=${config.rcloneTransfers}, chunk=${config.rcloneDriveChunk}, tps=${config.tpsLimit}`);
  if (config.maxBandwidth > 0) {
    console.log(`  Bandwidth  : ${formatSize(config.maxBandwidth)}/s cap`);
  }
  console.log(`${chalk.cyan(SEPARATOR)}\n`);
}

/**
 * Print upload result summary.
 */
function printSummary(success, filepath, fileSize, elapsed, wasCompressed = false, compressedSize = 0, error = '') {
  console.log(`\n${chalk.cyan(SEPARATOR)}`);
  console.log(`${chalk.cyan('  Upload Summary')}`);
  console.log(`${chalk.cyan(SEPARATOR)}`);

  if (success) {
    const speed = elapsed > 0 ? fileSize / elapsed : 0;
    console.log(`${chalk.green('  Status     : ✓ COMPLETE')}`);
    console.log(`  File       : ${path.basename(filepath)}`);
    console.log(`  Size       : ${formatSize(fileSize)}`);
    if (wasCompressed && compressedSize > 0) {
      const ratio = ((1 - compressedSize / fileSize) * 100).toFixed(1);
      console.log(`  Compressed : ${formatSize(compressedSize)} (${ratio}% smaller)`);
    }
    console.log(`  Time       : ${formatDuration(elapsed)}`);
    console.log(`  Avg Speed  : ${formatSize(Math.floor(speed))}/s`);
  } else {
    console.log(`${chalk.red('  Status     : ✗ FAILED')}`);
    console.log(`  File       : ${path.basename(filepath)}`);
    if (error) {
      console.log(`${chalk.red(`  Error      : ${error}`)}`);
    }
    console.log(`${chalk.yellow("  → Run 'cloudboost resume' to retry")}`);
  }

  console.log(`${chalk.cyan(SEPARATOR)}\n`);
}

/**
 * Upload a single file with smart compression and optimized rclone.
 *
 * Pipeline:
 * 1. Validate inputs
 * 2. Check if already uploaded (dedup)
 * 3. Analyze entropy → decide compression
 * 4. Compress if beneficial (zstd/gzip)
 * 5. Upload via rclone (single call, optimized flags)
 * 6. Track quota + save manifest
 */
export async function uploadFile(filepath, destination, config = null, quota = null) {
  ensureDirs();

  // ─── Validate ──────────────────────────────────────────────────────────────
  filepath = path.resolve(String(filepath).trim().replace(/^['"]|['"]$/g, ''));
  destination = await normalizeDestination(destination, config?.provider ? `${config.provider}:` : 'gdrive:');

  if (!fs.existsSync(filepath)) {
    console.error(chalk.red(`✗ File not found: ${filepath}`));
    return false;
  }

  if (!fs.statSync(filepath).isFile()) {
    console.error(chalk.red(`✗ Not a file: ${filepath} — use uploadFolder for directories`));
    return false;
  }

  if (!checkRcloneInstalled()) {
    console.error(chalk.red('✗ rclone not installed. Get it from https://rclone.org/install/'));
    return false;
  }

  const fileSize = fs.statSync(filepath).size;
  if (fileSize === 0) {
    console.error(chalk.red('✗ File is empty — nothing to upload'));
    return false;
  }

  // ─── Auto-tune config ──────────────────────────────────────────────────────
  if (config === null) {
    config = UploadConfig.autoTune(fileSize);
  }

  // ─── Dedup check ───────────────────────────────────────────────────────────
  if (config.dedup && isAlreadyUploaded(filepath, destination)) {
    console.log(chalk.green(`→ Already uploaded: ${path.basename(filepath)} → skipping`));
    return true;
  }

  // ─── Server-side copy detection ────────────────────────────────────────────
  const mountInfo = await detectRcloneMount(filepath);
  if (mountInfo) {
    const { remote: sourceRemote, remotePath } = mountInfo;
    const destRemote = destination.includes(':') ? destination.slice(0, destination.indexOf(':') + 1) : '';

    if (destRemote && sourceRemote.toLowerCase() === destRemote.toLowerCase()) {
      console.log(`\n${chalk.cyan(SEPARATOR)}`);
      console.log(`${chalk.green('  ⚡ SERVER-SIDE COPY — Zero Local Bandwidth!')}`);
      console.log(`${chalk.cyan(SEPARATOR)}`);
      console.log(`  File       : ${path.basename(filepath)}`);
      console.log(`  Size       : ${formatSize(fileSize)}`);
      console.log(`  Source     : ${sourceRemote}${remotePath}`);
      console.log(`  Target     : ${destination}`);
      console.log(`${chalk.green('  Mode       : Drive server-side copy (instant)')}`);
      console.log(`${chalk.cyan(SEPARATOR)}\n`);

      const manifest = createManifest(filepath);
      markUploading(filepath, manifest, destination);

      const start = performance.now();

      const progressBar = new AnimatedProgressBar(fileSize);
      progressBar.start('Server-side copy in progress...');

      const { success, error } = await serverSideCopy(
        sourceRemote, remotePath, destination,
        (line) => progressBar.updateFromRclone(line)
      );

      const elapsed = (performance.now() - start) / 1000;

      if (success) {
        progressBar.complete();
        markCompleted(filepath, manifest);
      } else {
        progressBar.fail();
        markFailed(filepath, manifest, error);
      }

      printSummary(success, filepath, fileSize, elapsed, false, 0, error);
      return success;
    }
  }

  // ─── Print header ──────────────────────────────────────────────────────────
  printHeader(filepath, destination, config, fileSize);

  // ─── Create manifest ───────────────────────────────────────────────────────
  const manifest = createManifest(filepath);
  markUploading(filepath, manifest, destination);

  // ─── Quota check ───────────────────────────────────────────────────────────
  if (quota === null) {
    quota = new QuotaTracker(config.provider);
  }

  const quotaStatus = quota.check();
  if (quotaStatus === 'blocked') {
    console.error(chalk.red('✗ Daily upload quota (750GB) reached. Try again tomorrow.'));
    markFailed(filepath, manifest, 'Quota exceeded');
    return false;
  }
  if (quotaStatus === 'warning') {
    console.log(chalk.yellow(`  ⚠ Approaching daily quota — ${formatSize(quota.remaining())} left`));
  }

  // ─── Small file fast path (< 50MB) ─────────────────────────────────────────
  if (fileSize < 50 * MB) {
    console.log(`→ Small file (${formatSize(fileSize)}) → direct upload`);
    const start = performance.now();
    const { success, error } = uploadFileRclone(filepath, destination, config, quota);
    const elapsed = (performance.now() - start) / 1000;

    if (success) {
      markCompleted(filepath, manifest);
    } else {
      markFailed(filepath, manifest, error);
    }

    printSummary(success, filepath, fileSize, elapsed, false, 0, error);
    return success;
  }

  // ─── Entropy analysis + compression ────────────────────────────────────────
  console.log('→ Analyzing file entropy...');
  const decision = analyzeFile(filepath);
  console.log(`  → ${decision.toString()}`);

  let uploadPath = filepath;
  let wasCompressed = false;
  let compressedSize = 0;

  if (decision.shouldCompress) {
    console.log(`→ Compressing with ${getCompressorName()} (level ${decision.level})...`);
    const compressStart = performance.now();

    // Create temp compressed file
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cloudboost_'));
    const compressedPath = path.join(tempDir, path.basename(filepath) + getCompressionExt());

    const result = await compressFile(filepath, compressedPath, decision);
    const compressElapsed = (performance.now() - compressStart) / 1000;

    if (result.wasCompressed) {
      wasCompressed = true;
      compressedSize = result.outputSize;
      uploadPath = result.outputPath;
      const ratio = ((1 - result.outputSize / fileSize) * 100).toFixed(1);
      console.log(`  → Compressed: ${formatSize(result.outputSize)} (${ratio}% smaller, took ${formatDuration(compressElapsed)})`);
      markCompressed(filepath, manifest, compressedPath, result.outputSize, getCompressorName());
    } else {
      console.log('  → Compression skipped (no benefit)');
      // Clean up temp dir
      try { fs.rmSync(tempDir, { recursive: true, force: true }); } catch { /* ignore */ }
    }
  } else {
    console.log(`  → Compression skipped: ${decision.reason}`);
  }

  // ─── Upload via rclone ─────────────────────────────────────────────────────
  const actualUploadSize = fs.statSync(uploadPath).size;
  console.log(`→ Uploading ${formatSize(actualUploadSize)} via rclone (${config.rcloneTransfers} transfers, chunk=${config.rcloneDriveChunk})...`);

  const uploadStart = performance.now();

  const progressBar = new AnimatedProgressBar(actualUploadSize);
  progressBar.start('Connecting to remote...');

  const progressCb = (line) => {
    progressBar.updateFromRclone(line);
  };

  const { success, error } = await uploadWithProgress(uploadPath, destination, config, quota, progressCb);
  if (success) {
    progressBar.complete();
  } else {
    progressBar.fail();
  }

  const uploadElapsed = (performance.now() - uploadStart) / 1000;

  // ─── Cleanup compressed temp file ──────────────────────────────────────────
  if (wasCompressed && uploadPath !== filepath) {
    const tempDir = path.dirname(uploadPath);
    try { fs.rmSync(tempDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }

  // ─── Record result ─────────────────────────────────────────────────────────
  if (success) {
    markCompleted(filepath, manifest);
  } else {
    markFailed(filepath, manifest, error);
  }

  printSummary(success, filepath, fileSize, uploadElapsed, wasCompressed, compressedSize, error);
  return success;
}

/**
 * Upload a folder with smart pre-processing.
 *
 * Strategy:
 * 1. Scan all files
 * 2. Separate into compressible vs already-compressed
 * 3. For compressible files: compress into staging dir
 * 4. Upload everything via single rclone copy call
 */
export async function uploadFolder(folderpath, destination, config = null, quota = null) {
  ensureDirs();

  folderpath = String(folderpath).trim().replace(/^['"]|['"]$/g, '');
  destination = await normalizeDestination(destination, config?.provider ? `${config.provider}:` : 'gdrive:');
  const folder = path.resolve(folderpath);
  if (!fs.existsSync(folder) || !fs.statSync(folder).isDirectory()) {
    console.error(chalk.red(`✗ Folder not found: ${folderpath}`));
    return false;
  }

  if (!checkRcloneInstalled()) {
    console.error(chalk.red('✗ rclone not installed.'));
    return false;
  }

  // Collect files
  const files = collectFiles(folder);
  if (files.length === 0) {
    console.error(chalk.red('✗ Folder is empty'));
    return false;
  }

  const totalSize = files.reduce((sum, f) => sum + fs.statSync(f).size, 0);

  if (config === null) {
    config = UploadConfig.autoTune(totalSize);
  }

  if (quota === null) {
    quota = new QuotaTracker(config.provider);
  }

  console.log(`\n${chalk.cyan(SEPARATOR)}`);
  console.log(`${chalk.cyan('  ⚡ cloud-boost v3 — Folder Upload')}`);
  console.log(`${chalk.cyan(SEPARATOR)}`);
  console.log(`  Folder     : ${folder}`);
  console.log(`  Files      : ${files.length}`);
  console.log(`  Total Size : ${formatSize(totalSize)}`);
  console.log(`  Target     : ${destination}`);
  console.log(`  Provider   : ${config.provider}`);
  console.log(`${chalk.cyan(SEPARATOR)}\n`);

  // ─── Analyze all files ─────────────────────────────────────────────────────
  console.log('→ Analyzing files for compression...');
  const compressible = [];
  const skipCompress = [];

  for (const f of files) {
    const decision = analyzeFile(f);
    const fSize = fs.statSync(f).size;
    if (decision.shouldCompress && fSize > 1 * MB) {
      compressible.push({ path: f, decision });
    } else {
      skipCompress.push(f);
    }
  }

  console.log(`  → ${compressible.length} files to compress, ${skipCompress.length} already compressed/small`);

  // ─── Create staging directory with compressed files ────────────────────────
  let stagingDir = null;
  let uploadSource;

  if (compressible.length > 0) {
    stagingDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cloudboost_stage_'));
    console.log(`→ Pre-compressing ${compressible.length} files...`);

    let compressedCount = 0;
    let savedBytes = 0;

    for (const { path: filePath, decision } of compressible) {
      const rel = path.relative(folder, filePath);
      const stagedPath = path.join(stagingDir, rel);
      fs.mkdirSync(path.dirname(stagedPath), { recursive: true });

      const result = await compressFile(filePath, stagedPath + getCompressionExt(), decision);

      if (result.wasCompressed) {
        compressedCount++;
        savedBytes += fs.statSync(filePath).size - result.outputSize;
      } else {
        // Copy original to staging
        copyFile(filePath, stagedPath);
      }
    }

    // Copy non-compressible files to staging
    for (const f of skipCompress) {
      const rel = path.relative(folder, f);
      const stagedPath = path.join(stagingDir, rel);
      copyFile(f, stagedPath);
    }

    if (compressedCount > 0) {
      console.log(`  → Compressed ${compressedCount} files, saved ${formatSize(savedBytes)}`);
    }

    uploadSource = stagingDir;
  } else {
    // Nothing to compress — upload folder directly
    uploadSource = folder;
  }

  // ─── Upload via single rclone call ─────────────────────────────────────────
  console.log(`→ Uploading via rclone (${config.rcloneTransfers} parallel transfers)...`);
  const uploadStart = performance.now();

  const progressBar = new AnimatedProgressBar(totalSize);
  progressBar.start(`${files.length} files queued...`);

  const folderProgressCb = (line) => {
    progressBar.updateFromRclone(line);
  };

  const { success, error } = await uploadWithProgress(uploadSource, destination, config, quota, folderProgressCb);
  if (success) {
    progressBar.complete();
  } else {
    progressBar.fail();
  }

  const elapsed = (performance.now() - uploadStart) / 1000;

  // ─── Cleanup staging ───────────────────────────────────────────────────────
  if (stagingDir) {
    try { fs.rmSync(stagingDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }

  // ─── Summary ───────────────────────────────────────────────────────────────
  console.log(`\n${chalk.cyan(SEPARATOR)}`);
  console.log(`${chalk.cyan('  Folder Upload Summary')}`);
  console.log(`${chalk.cyan(SEPARATOR)}`);

  if (success) {
    const speed = elapsed > 0 ? totalSize / elapsed : 0;
    console.log(`${chalk.green('  Status     : ✓ COMPLETE')}`);
    console.log(`  Files      : ${files.length}`);
    console.log(`  Total      : ${formatSize(totalSize)}`);
    console.log(`  Time       : ${formatDuration(elapsed)}`);
    console.log(`  Avg Speed  : ${formatSize(Math.floor(speed))}/s`);
    const quotaPct = quota.usagePct();
    if (quotaPct > 0) {
      console.log(`  Quota Used : ${quotaPct.toFixed(1)}% of 750GB daily limit`);
    }
  } else {
    console.log(`${chalk.red('  Status     : ✗ FAILED')}`);
    if (error) {
      console.log(`${chalk.red(`  Error      : ${error}`)}`);
    }
  }

  console.log(`${chalk.cyan(SEPARATOR)}\n`);
  return success;
}
