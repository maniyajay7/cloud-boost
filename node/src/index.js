/**
 * cloud-boost core uploader v2 (Node.js)
 * Production-grade: streaming pipeline, 100GB+ support, integrity verification
 */

'use strict';

const fs = require('fs-extra');
const path = require('path');
const zlib = require('zlib');
const os = require('os');
const cliProgress = require('cli-progress');

const { autoTune, formatSize, formatDuration, MB, GB, ensureDirs } = require('./config');
const {
  computeSha256, computeChunkHash,
  loadManifest, saveManifest, clearManifest,
  isChunkUploaded, markChunkUploaded,
} = require('./integrity');
const {
  checkRcloneInstalled, getRcloneRemotes,
  uploadChunk: rcloneUploadChunk, uploadDirectRclone,
} = require('./transport');

/**
 * Stream-read a chunk from file at offset, compress in memory.
 * NEVER writes raw chunk to disk — only compressed result touches disk.
 */
async function streamCompressChunk(filepath, offset, size, level) {
  const fd = await fs.open(filepath, 'r');
  const buffer = Buffer.alloc(size);
  const { bytesRead } = await fs.read(fd, buffer, 0, size, offset);
  await fs.close(fd);

  const rawData = buffer.slice(0, bytesRead);
  const rawHash = computeChunkHash(rawData);

  const compressed = await new Promise((resolve, reject) => {
    zlib.gzip(rawData, { level }, (err, result) => {
      if (err) reject(err);
      else resolve(result);
    });
  });

  return { compressed, rawHash };
}

/**
 * Process a single chunk: read → compress → write temp → upload → delete temp.
 */
async function processAndUploadChunk(
  filepath, chunkIndex, offset, chunkSize, destination, config, manifest
) {
  const result = {
    chunkIndex,
    success: false,
    skipped: false,
    rawSize: chunkSize,
    compressedSize: 0,
    uploadTime: 0,
  };

  const tempPath = path.join(os.tmpdir(), `cb_${String(chunkIndex).padStart(6, '0')}.gz`);

  try {
    // Step 1 — Stream read + compress in memory
    const { compressed, rawHash } = await streamCompressChunk(
      filepath, offset, chunkSize, config.compressionLevel
    );
    result.compressedSize = compressed.length;

    // Step 2 — Dedup + resume check
    if (config.dedup && isChunkUploaded(manifest, chunkIndex, rawHash)) {
      result.success = true;
      result.skipped = true;
      return result;
    }

    // Step 3 — Write compressed to temp (only compressed touches disk)
    await fs.writeFile(tempPath, compressed);

    // Step 4 — Upload via rclone
    const startTime = Date.now();
    const success = await rcloneUploadChunk(tempPath, destination, chunkIndex, config);
    result.uploadTime = (Date.now() - startTime) / 1000;

    if (success) {
      markChunkUploaded(manifest, chunkIndex, rawHash, result.compressedSize);
      await saveManifest(filepath, manifest);
      result.success = true;
    }
  } catch (err) {
    result.error = err.message;
  } finally {
    // Always clean up temp
    await fs.remove(tempPath).catch(() => {});
  }

  return result;
}

/**
 * Main upload entry point for a single file.
 */
async function uploadFile(filepath, destination, config = null) {
  const chalk = (await import('chalk')).default;
  const pLimit = (await import('p-limit')).default;

  await ensureDirs();

  // ─── Validations ─────────────────────────────────────────────────────────
  if (!(await fs.pathExists(filepath))) {
    console.error(chalk.red(`✗ File not found: ${filepath}`));
    return false;
  }

  const stat = await fs.stat(filepath);
  if (!stat.isFile()) {
    console.error(chalk.red(`✗ Not a file: ${filepath} (use uploadFolder for directories)`));
    return false;
  }

  if (!checkRcloneInstalled()) {
    console.error(chalk.red('✗ rclone is not installed. Install from https://rclone.org/install/'));
    return false;
  }

  const fileSize = stat.size;
  if (fileSize === 0) {
    console.error(chalk.red('✗ File is empty — nothing to upload'));
    return false;
  }

  // ─── Auto-tune config ────────────────────────────────────────────────────
  if (!config) {
    config = autoTune(fileSize);
  }

  // ─── Small file fast path ────────────────────────────────────────────────
  if (fileSize < 50 * MB) {
    console.log(chalk.cyan(`\n→ Small file (${formatSize(fileSize)}) — direct rclone upload`));
    const start = Date.now();
    const destPath = `${destination}/${path.basename(filepath)}`;
    const success = await uploadDirectRclone(filepath, destPath, config);
    const elapsed = (Date.now() - start) / 1000;
    if (success) {
      const speed = fileSize / elapsed;
      console.log(chalk.green(`✓ Uploaded in ${formatDuration(elapsed)} (${formatSize(Math.floor(speed))}/s)\n`));
    } else {
      console.error(chalk.red('✗ Direct upload failed\n'));
    }
    return success;
  }

  // ─── Calculate chunks ────────────────────────────────────────────────────
  const totalChunks = Math.ceil(fileSize / config.chunkSize);

  const separator = '═'.repeat(60);
  console.log(`\n${chalk.cyan(separator)}`);
  console.log(chalk.cyan('  ⚡ cloud-boost v2 — Production Upload Engine'));
  console.log(chalk.cyan(separator));
  console.log(`  File       : ${path.basename(filepath)}`);
  console.log(`  Size       : ${formatSize(fileSize)}`);
  console.log(`  Target     : ${destination}`);
  console.log(`  Chunks     : ${totalChunks} × ${formatSize(config.chunkSize)}`);
  console.log(`  Threads    : ${config.threads}`);
  console.log(`  Compress   : level ${config.compressionLevel}`);
  console.log(`  Integrity  : SHA-256 ${config.checksumVerify ? 'ON' : 'OFF'}`);
  console.log(`  Resume     : ${config.resume ? 'ON' : 'OFF'}`);
  if (config.maxBandwidth > 0) {
    console.log(`  Bandwidth  : ${formatSize(config.maxBandwidth)}/s cap`);
  }
  console.log(chalk.cyan(separator) + '\n');

  // ─── Load manifest (resume support) ──────────────────────────────────────
  let manifest = await loadManifest(filepath);

  if (!manifest.source_hash && config.checksumVerify) {
    console.log(chalk.cyan('→ Computing source file hash (SHA-256)...'));
    manifest.source_hash = await computeSha256(filepath);
    manifest.total_chunks = totalChunks;
    manifest.chunk_size = config.chunkSize;
    await saveManifest(filepath, manifest);
    console.log(`  Hash: ${manifest.source_hash.slice(0, 16)}...`);
  }

  // ─── Count already-done chunks ───────────────────────────────────────────
  const alreadyDone = Object.values(manifest.chunks).filter(c => c.uploaded).length;
  if (alreadyDone > 0) {
    console.log(chalk.magenta(`→ Resuming — ${alreadyDone}/${totalChunks} chunks already uploaded\n`));
  }

  // ─── Build chunk tasks ───────────────────────────────────────────────────
  const chunkTasks = [];
  for (let i = 0; i < totalChunks; i++) {
    const offset = i * config.chunkSize;
    const actualSize = Math.min(config.chunkSize, fileSize - offset);
    chunkTasks.push({ index: i, offset, size: actualSize });
  }

  // ─── Parallel upload ─────────────────────────────────────────────────────
  let totalUploaded = 0;
  let totalSkipped = 0;
  let totalFailed = 0;
  let totalCompressedBytes = 0;
  const uploadStart = Date.now();

  const bar = new cliProgress.SingleBar({
    format: '  Uploading |{bar}| {value}/{total} chunks [{duration_formatted}<{eta_formatted}]',
  }, cliProgress.Presets.shades_classic);
  bar.start(totalChunks, 0);

  const limit = pLimit(config.threads);

  const promises = chunkTasks.map(task =>
    limit(async () => {
      const result = await processAndUploadChunk(
        filepath, task.index, task.offset, task.size, destination, config, manifest
      );

      if (result.success) {
        if (result.skipped) {
          totalSkipped++;
        } else {
          totalUploaded++;
          totalCompressedBytes += result.compressedSize;
        }
      } else {
        totalFailed++;
        console.error(chalk.red(`\n  ✗ Chunk ${result.chunkIndex} failed${result.error ? ': ' + result.error : ''}`));
      }

      bar.increment();
      return result;
    })
  );

  await Promise.all(promises);
  bar.stop();

  const totalElapsed = (Date.now() - uploadStart) / 1000;

  // ─── Summary ─────────────────────────────────────────────────────────────
  console.log(`\n${chalk.cyan(separator)}`);
  console.log(chalk.cyan('  Upload Summary'));
  console.log(chalk.cyan(separator));

  if (totalFailed === 0) {
    const avgSpeed = fileSize / totalElapsed;
    const compressionRatio = totalCompressedBytes > 0 && fileSize > 0
      ? ((1 - totalCompressedBytes / fileSize) * 100)
      : 0;

    console.log(chalk.green('  Status     : ✓ COMPLETE'));
    console.log(`  Uploaded   : ${totalUploaded} chunks`);
    if (totalSkipped > 0) {
      console.log(chalk.magenta(`  Skipped    : ${totalSkipped} (already uploaded)`));
    }
    console.log(`  Time       : ${formatDuration(totalElapsed)}`);
    console.log(`  Avg Speed  : ${formatSize(Math.floor(avgSpeed))}/s`);
    if (compressionRatio > 0) {
      console.log(`  Compressed : ${compressionRatio.toFixed(1)}% smaller`);
    }
    console.log(chalk.cyan(separator) + '\n');

    manifest.completed = true;
    await saveManifest(filepath, manifest);
    return true;
  } else {
    console.log(chalk.red('  Status     : ✗ INCOMPLETE'));
    console.log(`  Uploaded   : ${totalUploaded}/${totalChunks}`);
    console.log(chalk.red(`  Failed     : ${totalFailed}`));
    console.log(chalk.yellow("  → Run 'cloudboost resume' to retry failed chunks"));
    console.log(chalk.cyan(separator) + '\n');
    return false;
  }
}

/**
 * Upload all files in a folder recursively.
 */
async function uploadFolder(folderpath, destination, config = null) {
  const chalk = (await import('chalk')).default;

  if (!(await fs.pathExists(folderpath))) {
    console.error(chalk.red(`✗ Folder not found: ${folderpath}`));
    return false;
  }

  const stat = await fs.stat(folderpath);
  if (!stat.isDirectory()) {
    console.error(chalk.red(`✗ Not a directory: ${folderpath}`));
    return false;
  }

  // Collect all files recursively
  const allFiles = [];
  async function walk(dir) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(fullPath);
      } else if (entry.isFile()) {
        allFiles.push(fullPath);
      }
    }
  }
  await walk(folderpath);

  if (allFiles.length === 0) {
    console.error(chalk.red('✗ Folder is empty — nothing to upload'));
    return false;
  }

  let totalSize = 0;
  for (const f of allFiles) {
    totalSize += (await fs.stat(f)).size;
  }

  console.log(chalk.cyan(`\n→ Found ${allFiles.length} files (${formatSize(totalSize)}) in ${folderpath}`));

  let successCount = 0;
  let failCount = 0;

  for (let i = 0; i < allFiles.length; i++) {
    const file = allFiles[i];
    const relPath = path.relative(folderpath, file);
    const fileDest = path.dirname(relPath) !== '.'
      ? `${destination}/${path.dirname(relPath)}`
      : destination;

    console.log(`\n[${i + 1}/${allFiles.length}] ${relPath}`);

    if (await uploadFile(file, fileDest, config)) {
      successCount++;
    } else {
      failCount++;
    }
  }

  // Final summary
  const separator = '═'.repeat(60);
  console.log(`\n${chalk.cyan(separator)}`);
  console.log(chalk.cyan('  Folder Upload Summary'));
  console.log(chalk.cyan(separator));
  console.log(chalk.green(`  Success : ${successCount}/${allFiles.length}`));
  if (failCount > 0) {
    console.log(chalk.red(`  Failed  : ${failCount}`));
  }
  console.log(chalk.cyan(separator) + '\n');

  return failCount === 0;
}

module.exports = { uploadFile, uploadFolder };
