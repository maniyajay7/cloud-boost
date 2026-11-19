#!/usr/bin/env node

/**
 * cloud-boost v3 CLI
 * Production CLI with entropy-aware compression, quota tracking, setup wizard.
 */

import { Command } from 'commander';
import chalk from 'chalk';
import fs from 'fs';
import path from 'path';
import process from 'process';
import {
  UploadConfig,
  parseSize,
  formatSize,
  saveDefaultConfig,
  loadConfig,
  ensureDirs,
  MB,
} from './config.js';
import { uploadFile, uploadFolder } from './index.js';
import {
  checkRcloneInstalled,
  getRcloneRemotes,
  QuotaTracker,
} from './transport.js';
import { printSetupGuide, checkCustomClientId } from './setup-wizard.js';
import { getCompressorName } from './compressor.js';
import { listPendingUploads } from './manifest.js';

const BANNER = `${chalk.cyan(`
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST`)}
  ⚡ v3.0 — Production Upload Engine
  ⚡ Entropy-aware | zstd | Quota-safe | Auto-tuned
`;

const program = new Command();

program
  .name('cloudboost')
  .version('3.0.0')
  .description('⚡ cloud-boost — supercharge your cloud uploads');

// ─── upload ──────────────────────────────────────────────────────────────────
program
  .command('upload')
  .description('Upload FILE or FOLDER to cloud DESTINATION')
  .argument('<source>', 'Local file or folder path')
  .argument('<destination>', 'Cloud destination (e.g. gdrive:/backup/)')
  .option('-t, --threads <n>', 'Upload threads (auto-tuned)', parseInt)
  .option('--no-compress', 'Skip compression entirely')
  .option('--no-dedup', 'Disable deduplication')
  .option('--max-bw <size>', 'Bandwidth limit e.g. 50MB')
  .option('--max-transfer <size>', 'Max bytes to upload e.g. 700GB (quota safety)')
  .option('-p, --provider <name>', 'Cloud provider', 'gdrive')
  .option('-v, --verbose', 'Verbose output', false)
  .action(async (source, destination, opts) => {
    console.log(BANNER);

    // Check custom client ID
    if (opts.provider === 'gdrive' && !checkCustomClientId()) {
      console.log(chalk.yellow("  ⚠ You're using rclone's SHARED client ID."));
      console.log(chalk.yellow('    This is 5-10x SLOWER than a custom one.'));
      console.log(chalk.yellow("    Run 'cloudboost setup' for the speed fix.\n"));
    }

    const overrides = {
      provider: opts.provider,
      verbose: opts.verbose,
      compression: opts.compress !== false ? true : false,
      dedup: opts.dedup !== false ? true : false,
    };

    if (opts.threads !== undefined) {
      overrides.rcloneTransfers = opts.threads;
    }
    if (opts.maxBw) {
      overrides.maxBandwidth = parseSize(opts.maxBw);
    }
    if (opts.maxTransfer) {
      overrides.maxTransfer = parseSize(opts.maxTransfer);
    }

    const config = new UploadConfig(overrides);
    const quota = new QuotaTracker(opts.provider);

    let success;
    if (fs.existsSync(source) && fs.statSync(source).isDirectory()) {
      success = await uploadFolder(source, destination, config, quota);
    } else {
      success = await uploadFile(source, destination, config, quota);
    }

    process.exit(success ? 0 : 1);
  });

// ─── resume ──────────────────────────────────────────────────────────────────
program
  .command('resume')
  .description('Show and resume interrupted uploads')
  .action(() => {
    console.log(BANNER);
    const pending = listPendingUploads();

    if (pending.length === 0) {
      console.log(chalk.green('  No pending uploads found. All clean!\n'));
      return;
    }

    console.log(chalk.yellow(`  Found ${pending.length} interrupted upload(s):\n`));
    pending.forEach((m, i) => {
      const status = m.status || 'unknown';
      const color = status === 'uploading' ? chalk.yellow : chalk.red;
      console.log(`  ${i + 1}. ${color(`[${status.toUpperCase()}] ${m.source_name || '?'}`)}`);
      console.log(`     Size: ${formatSize(m.source_size || 0)}`);
      console.log(`     Dest: ${m.destination || '?'}`);
      if (m.error) {
        console.log(`     Error: ${m.error.slice(0, 80)}`);
      }
      console.log();
    });

    console.log("  → Re-run 'cloudboost upload <source> <dest>' to resume automatically.\n");
  });

// ─── doctor ──────────────────────────────────────────────────────────────────
program
  .command('doctor')
  .description('Diagnose cloud-boost setup — rclone, remotes, compressor, client ID')
  .action(() => {
    console.log(BANNER);
    console.log(chalk.cyan('→ Running diagnostics...\n'));

    let allOk = true;

    // rclone
    if (checkRcloneInstalled()) {
      console.log(chalk.green('  ✓ rclone installed'));
    } else {
      console.log(chalk.red('  ✗ rclone NOT found — https://rclone.org/install/'));
      allOk = false;
    }

    // Remotes
    const remotes = getRcloneRemotes();
    if (remotes.length > 0) {
      console.log(chalk.green(`  ✓ ${remotes.length} remote(s) configured:`));
      remotes.forEach(r => console.log(`    · ${r}`));
    } else {
      console.log(chalk.yellow("  ⚠ No remotes — run 'rclone config'"));
      allOk = false;
    }

    // Custom client ID
    if (checkCustomClientId()) {
      console.log(chalk.green('  ✓ Custom OAuth Client ID detected (fast mode)'));
    } else {
      console.log(chalk.yellow('  ⚠ Using SHARED client ID (5-10x slower)'));
      console.log("    → Run 'cloudboost setup' to fix this");
    }

    // Compressor
    const compressor = getCompressorName();
    if (compressor === 'zstd') {
      console.log(chalk.green('  ✓ zstd compression available (5-10x faster than gzip)'));
    } else {
      console.log(chalk.yellow('  ⚠ zstd not installed — using gzip fallback'));
      console.log('    → npm install @aspect-build/zstd');
    }

    // Config
    const userConfig = loadConfig();
    if (Object.keys(userConfig).length > 0) {
      console.log(chalk.green('  ✓ Config file loaded'));
    } else {
      console.log(chalk.yellow('  ⚠ Using defaults — customize at ~/.cloudboost/config.json'));
    }

    console.log();
    if (allOk) {
      console.log(chalk.green('  All critical checks passed!\n'));
    } else {
      console.log(chalk.red('  Some issues found — fix them above.\n'));
    }
  });

// ─── setup ───────────────────────────────────────────────────────────────────
program
  .command('setup')
  .description('Interactive setup guide for maximum upload speed')
  .action(() => {
    console.log(BANNER);
    printSetupGuide();
  });

// ─── stats ───────────────────────────────────────────────────────────────────
program
  .command('stats')
  .description('Show rclone config and cloud connection stats')
  .action(() => {
    console.log(BANNER);

    if (!checkRcloneInstalled()) {
      console.log(chalk.red('  ✗ rclone not installed\n'));
      return;
    }

    const remotes = getRcloneRemotes();
    console.log(chalk.cyan('→ Configured remotes:\n'));
    if (remotes.length > 0) {
      remotes.forEach(r => console.log(`  ✓ ${r}`));
    } else {
      console.log("  No remotes — run 'rclone config'");
    }

    console.log(`\n${chalk.cyan(`→ Compressor: ${getCompressorName()}`)}\n`);
  });

// ─── init ────────────────────────────────────────────────────────────────────
program
  .command('init')
  .description('Initialize cloud-boost config in ~/.cloudboost/')
  .action(() => {
    console.log(BANNER);
    ensureDirs();
    saveDefaultConfig();
    console.log(chalk.green('  ✓ Config initialized at ~/.cloudboost/\n'));
  });

program.parse(process.argv);
