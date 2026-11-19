#!/usr/bin/env node
/**
 * cloud-boost CLI v2 (Node.js)
 * Production CLI with auto-tuning, folder support, diagnostics
 */

'use strict';

const { Command } = require('commander');
const { uploadFile, uploadFolder } = require('./index');
const {
  autoTune, parseSize, formatSize, ensureDirs,
} = require('./config');
const {
  checkRcloneInstalled, getRcloneRemotes,
} = require('./transport');
const fs = require('fs-extra');
const path = require('path');

const BANNER = `
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST
  ⚡ v2.0 — Production Upload Engine
`;

const program = new Command();

program
  .name('cloudboost')
  .description('⚡ cloud-boost — supercharge your cloud uploads')
  .version('2.0.0');

program
  .command('upload <source> <destination>')
  .description('Upload a file or folder with parallel chunked upload')
  .option('-t, --threads <number>', 'Upload threads (auto-tuned by default)')
  .option('-c, --chunk-size <size>', 'Chunk size e.g. 8MB, 32MB (auto-tuned)')
  .option('--no-compress', 'Disable compression')
  .option('--compress-level <level>', 'Gzip level 1-9 (auto-tuned)')
  .option('--no-resume', 'Disable resume/checkpoint')
  .option('--no-dedup', 'Disable deduplication')
  .option('--no-checksum', 'Disable SHA-256 verification')
  .option('--max-bw <size>', 'Bandwidth limit e.g. 50MB, 100MB')
  .option('-p, --provider <name>', 'Cloud provider', 'gdrive')
  .option('-v, --verbose', 'Verbose output')
  .action(async (source, destination, options) => {
    console.log(BANNER);

    const overrides = {
      provider: options.provider,
      verbose: options.verbose || false,
      compression: options.compress !== false,
      resume: options.resume !== false,
      dedup: options.dedup !== false,
      checksumVerify: options.checksum !== false,
    };

    if (options.threads) overrides.threads = parseInt(options.threads);
    if (options.chunkSize) overrides.chunkSize = parseSize(options.chunkSize);
    if (options.compressLevel) overrides.compressionLevel = parseInt(options.compressLevel);
    if (options.maxBw) overrides.maxBandwidth = parseSize(options.maxBw);

    const config = autoTune(0, overrides); // size-based tuning happens in uploadFile

    const stat = await fs.stat(source).catch(() => null);
    if (!stat) {
      console.error(`✗ Source not found: ${source}`);
      process.exit(1);
    }

    let success;
    if (stat.isDirectory()) {
      success = await uploadFolder(source, destination, config);
    } else {
      success = await uploadFile(source, destination, config);
    }

    process.exit(success ? 0 : 1);
  });

program
  .command('resume <source> <destination>')
  .description('Resume an interrupted upload')
  .option('-v, --verbose', 'Verbose output')
  .action(async (source, destination, options) => {
    console.log(BANNER);
    console.log('→ Resuming upload:', source, '\n');

    const config = autoTune(0, { resume: true, verbose: options.verbose || false });
    const success = await uploadFile(source, destination, config);
    process.exit(success ? 0 : 1);
  });

program
  .command('doctor')
  .description('Diagnose cloud-boost setup — check rclone, remotes, config')
  .action(async () => {
    console.log(BANNER);
    console.log('→ Running diagnostics...\n');

    // Check rclone
    if (checkRcloneInstalled()) {
      console.log('  ✓ rclone installed');
    } else {
      console.error('  ✗ rclone NOT found — install from https://rclone.org/install/');
      return;
    }

    // Check remotes
    const remotes = getRcloneRemotes();
    if (remotes.length > 0) {
      console.log(`  ✓ ${remotes.length} remote(s) configured:`);
      remotes.forEach(r => console.log(`    · ${r}`));
    } else {
      console.log("  ⚠ No remotes configured — run 'rclone config'");
    }

    // Check config
    await ensureDirs();
    console.log('  ✓ Config directories ready');
    console.log('\n  All checks passed!\n');
  });

program
  .command('stats')
  .description('Show rclone config and cloud connection stats')
  .action(() => {
    console.log(BANNER);

    if (!checkRcloneInstalled()) {
      console.error('  ✗ rclone not installed\n');
      return;
    }

    const remotes = getRcloneRemotes();
    console.log('→ Configured remotes:\n');
    if (remotes.length > 0) {
      remotes.forEach(r => console.log(`  ✓ ${r}`));
    } else {
      console.log("  No remotes configured — run 'rclone config'");
    }
    console.log();
  });

program
  .command('init')
  .description('Initialize cloud-boost config')
  .action(async () => {
    console.log(BANNER);
    await ensureDirs();
    console.log('  ✓ Config initialized at ~/.cloudboost/');
    console.log('  Edit config.yml to customize your upload settings.\n');
  });

program.parse(process.argv);
