/**
 * cloud-boost transport layer (Node.js)
 * Handles: rclone subprocess execution, retries, bandwidth throttling
 */

'use strict';

const { execSync, spawn } = require('child_process');
const which = require('which');
const path = require('path');
const fs = require('fs-extra');
const { formatSize } = require('./config');

function checkRcloneInstalled() {
  try {
    which.sync('rclone');
    return true;
  } catch {
    return false;
  }
}

function getRcloneRemotes() {
  try {
    const output = execSync('rclone listremotes', { timeout: 10000 }).toString().trim();
    return output.split('\n').filter(Boolean).map(r => r.trim());
  } catch {
    return [];
  }
}

function buildRcloneCommand(source, destination, config) {
  const cmd = [
    'rclone', 'copyto',
    source, destination,
    `--transfers=${config.rcloneTransfers}`,
    `--buffer-size=${config.rcloneBufferSize}`,
    '--checkers=16',
    '--contimeout=30s',
    '--timeout=300s',
    '--retries=3',
    '--low-level-retries=5',
    '--stats=0',
  ];

  if (config.provider === 'gdrive') {
    cmd.push(`--drive-chunk-size=${config.rcloneDriveChunk}`);
    cmd.push('--drive-upload-cutoff=8M');
    cmd.push('--drive-use-trash=false');
  }

  if (config.maxBandwidth > 0) {
    cmd.push(`--bwlimit=${formatSize(config.maxBandwidth).replace(' ', '')}`);
  }

  return cmd;
}

function uploadChunk(chunkPath, destination, chunkIndex, config) {
  return new Promise((resolve) => {
    const subDir = Math.floor(chunkIndex / 256).toString(16).padStart(2, '0');
    const chunkName = `chunk_${String(chunkIndex).padStart(6, '0')}.gz`;
    const destPath = `${destination}/.cb_upload/${subDir}/${chunkName}`;

    let attempt = 0;
    const maxRetries = config.maxRetries || 3;

    function tryUpload() {
      attempt++;
      const cmd = buildRcloneCommand(chunkPath, destPath, config);
      const proc = spawn(cmd[0], cmd.slice(1));

      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        proc.kill();
      }, 600000); // 10 min

      proc.on('close', (code) => {
        clearTimeout(timer);
        if (code === 0) {
          resolve(true);
        } else if (attempt < maxRetries) {
          const delay = config.retryDelay * Math.pow(2, attempt - 1) * 1000;
          setTimeout(tryUpload, delay);
        } else {
          resolve(false);
        }
      });

      proc.on('error', () => {
        clearTimeout(timer);
        if (attempt < maxRetries) {
          const delay = config.retryDelay * Math.pow(2, attempt - 1) * 1000;
          setTimeout(tryUpload, delay);
        } else {
          resolve(false);
        }
      });
    }

    tryUpload();
  });
}

function uploadDirectRclone(source, destination, config) {
  return new Promise((resolve) => {
    const cmd = buildRcloneCommand(source, destination, config);
    const proc = spawn(cmd[0], cmd.slice(1));
    const timer = setTimeout(() => { proc.kill(); resolve(false); }, 3600000);
    proc.on('close', (code) => { clearTimeout(timer); resolve(code === 0); });
    proc.on('error', () => { clearTimeout(timer); resolve(false); });
  });
}

module.exports = {
  checkRcloneInstalled,
  getRcloneRemotes,
  buildRcloneCommand,
  uploadChunk,
  uploadDirectRclone,
};
