/**
 * cloud-boost core uploader (Node.js)
 * Handles: chunking, compression, parallel upload, resume
 */

'use strict';

const fs = require('fs-extra');
const path = require('path');
const zlib = require('zlib');
const { execSync, spawn } = require('child_process');
const cliProgress = require('cli-progress');

const DEFAULT_CHUNK_SIZE_MB = 5;
const DEFAULT_THREADS = 6;
const CHECKPOINT_EXT = '.cbcheckpoint';

// ─── Chunk a file into pieces ─────────────────────────────────────────────────
async function chunkFile(filepath, chunkSizeMb) {
  const chunkSize = chunkSizeMb * 1024 * 1024;
  const chunkDir = path.join(
    path.dirname(filepath),
    `.cb_chunks_${path.basename(filepath, path.extname(filepath))}`
  );
  await fs.ensureDir(chunkDir);

  const chunks = [];
  const fd = await fs.open(filepath, 'r');
  let index = 0;
  let position = 0;

  while (true) {
    const buffer = Buffer.alloc(chunkSize);
    const { bytesRead } = await fs.read(fd, buffer, 0, chunkSize, position);
    if (bytesRead === 0) break;

    const chunkPath = path.join(chunkDir, `chunk_${String(index).padStart(4, '0')}.bin`);
    await fs.writeFile(chunkPath, buffer.slice(0, bytesRead));
    chunks.push(chunkPath);
    position += bytesRead;
    index++;
  }

  await fs.close(fd);
  return chunks;
}

// ─── Compress a chunk with gzip ───────────────────────────────────────────────
function compressChunk(chunkPath) {
  return new Promise((resolve, reject) => {
    const compressedPath = chunkPath + '.gz';
    const input = fs.createReadStream(chunkPath);
    const output = fs.createWriteStream(compressedPath);
    const gzip = zlib.createGzip();
    input.pipe(gzip).pipe(output);
    output.on('finish', () => resolve(compressedPath));
    output.on('error', reject);
  });
}

// ─── Upload single chunk via rclone ───────────────────────────────────────────
function uploadChunkRclone(chunkPath, destination, chunkIndex) {
  return new Promise((resolve) => {
    const destPath = `${destination}/chunks/chunk_${String(chunkIndex).padStart(4, '0')}.gz`;
    const proc = spawn('rclone', ['copyto', chunkPath, destPath]);
    proc.on('close', (code) => resolve(code === 0));
    proc.on('error', () => resolve(false));
  });
}

// ─── Checkpoint helpers ───────────────────────────────────────────────────────
async function saveCheckpoint(filepath, uploadedChunks) {
  const checkpointPath = filepath + CHECKPOINT_EXT;
  await fs.writeJSON(checkpointPath, { uploadedChunks });
}

async function loadCheckpoint(filepath) {
  const checkpointPath = filepath + CHECKPOINT_EXT;
  if (await fs.pathExists(checkpointPath)) {
    const data = await fs.readJSON(checkpointPath);
    return data.uploadedChunks || [];
  }
  return [];
}

async function cleanup(filepath) {
  const chunkDir = path.join(
    path.dirname(filepath),
    `.cb_chunks_${path.basename(filepath, path.extname(filepath))}`
  );
  if (await fs.pathExists(chunkDir)) await fs.remove(chunkDir);
  const checkpoint = filepath + CHECKPOINT_EXT;
  if (await fs.pathExists(checkpoint)) await fs.remove(checkpoint);
}

// ─── Main upload function ─────────────────────────────────────────────────────
async function upload({
  filepath,
  destination,
  threads = DEFAULT_THREADS,
  chunkSizeMb = DEFAULT_CHUNK_SIZE_MB,
  compress = true,
}) {
  const { default: chalk } = await import('chalk');
  const pLimit = (await import('p-limit')).default;

  if (!(await fs.pathExists(filepath))) {
    console.error(chalk.red(`✗ File not found: ${filepath}`));
    return false;
  }

  const stats = await fs.stat(filepath);
  const fileSizeMb = (stats.size / (1024 * 1024)).toFixed(1);

  console.log(`\n${chalk.cyan('⚡ cloud-boost starting upload')}`);
  console.log(`   File     : ${filepath} (${fileSizeMb} MB)`);
  console.log(`   Target   : ${destination}`);
  console.log(`   Threads  : ${threads}`);
  console.log(`   Chunk    : ${chunkSizeMb} MB`);
  console.log(`   Compress : ${compress ? 'yes' : 'no'}\n`);

  // Step 1 — Split
  console.log(chalk.yellow('→ Splitting into chunks...'));
  let chunks = await chunkFile(filepath, chunkSizeMb);
  console.log(`  ${chunks.length} chunks created\n`);

  // Step 2 — Compress
  if (compress) {
    console.log(chalk.yellow('→ Compressing chunks...'));
    const bar = new cliProgress.SingleBar({}, cliProgress.Presets.shades_classic);
    bar.start(chunks.length, 0);
    const compressed = [];
    for (const c of chunks) {
      compressed.push(await compressChunk(c));
      bar.increment();
    }
    bar.stop();
    chunks = compressed;
    console.log();
  }

  // Step 3 — Resume check
  const alreadyUploaded = await loadCheckpoint(filepath);
  if (alreadyUploaded.length > 0) {
    console.log(chalk.magenta(`→ Resuming — ${alreadyUploaded.length} chunks already done\n`));
  }

  // Step 4 — Parallel upload
  console.log(chalk.yellow(`→ Uploading ${chunks.length} chunks with ${threads} parallel threads...`));
  const bar = new cliProgress.SingleBar({}, cliProgress.Presets.shades_classic);
  const toUpload = chunks.filter((_, i) => !alreadyUploaded.includes(i));
  bar.start(toUpload.length, 0);

  const uploaded = [...alreadyUploaded];
  const failed = [];
  const limit = pLimit(threads);

  const tasks = chunks.map((chunk, i) => {
    if (alreadyUploaded.includes(i)) return Promise.resolve();
    return limit(async () => {
      const success = await uploadChunkRclone(chunk, destination, i);
      if (success) {
        uploaded.push(i);
        await saveCheckpoint(filepath, uploaded);
      } else {
        failed.push(i);
      }
      bar.increment();
    });
  });

  await Promise.all(tasks);
  bar.stop();

  // Step 5 — Result
  if (failed.length > 0) {
    console.error(chalk.red(`\n✗ ${failed.length} chunks failed. Run with resume to retry.`));
    return false;
  }

  await cleanup(filepath);
  console.log(chalk.green(`\n✓ Upload complete! ${chunks.length}/${chunks.length} chunks uploaded.`));
  console.log(chalk.green(`  Destination: ${destination}\n`));
  return true;
}

module.exports = { upload };
