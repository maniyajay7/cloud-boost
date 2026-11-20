#!/usr/bin/env node

/**
 * cloud-boost v3 CLI
 * Production CLI with entropy-aware compression, quota tracking, setup wizard.
 * Interactive mode when run without arguments.
 */

import { Command } from 'commander';
import chalk from 'chalk';
import fs from 'fs';
import path from 'path';
import process from 'process';
import readline from 'readline';
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
  normalizeDestination,
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

const SEP = '─'.repeat(50);

// ─── Interactive Wizard Helpers ──────────────────────────────────────────────

function createRl() {
  return readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
}

function ask(rl, query) {
  return new Promise((resolve) => rl.question(query, (ans) => resolve(ans.trim())));
}

async function promptChoice(rl, title, options, defaultIdx = 0) {
  console.log(`\n  ${chalk.cyan(title)}`);
  console.log(`  ${chalk.gray(SEP)}`);

  options.forEach((opt, i) => {
    const marker = i === defaultIdx ? chalk.green('●') : chalk.gray('○');
    const defaultTag = i === defaultIdx ? ` ${chalk.yellow('← default')}` : '';
    const descText = opt.desc ? ` ${chalk.gray(`— ${opt.desc}`)}` : '';
    console.log(`  ${marker} ${chalk.white(`${i + 1}. ${opt.label}`)}${descText}${defaultTag}`);
  });

  console.log(`  ${chalk.gray(SEP)}`);

  while (true) {
    const raw = await ask(rl, `  ${chalk.cyan('→')} Choose [1-${options.length}]: `);
    if (!raw) return options[defaultIdx];
    const idx = parseInt(raw, 10) - 1;
    if (idx >= 0 && idx < options.length) {
      return options[idx];
    }
    console.log(chalk.red(`    Invalid — enter a number 1-${options.length}`));
  }
}

async function promptInput(rl, label, defaultVal = '') {
  const hint = defaultVal ? ` ${chalk.gray(`[${defaultVal}]`)}` : '';
  const raw = await ask(rl, `  ${chalk.cyan('→')} ${label}${hint}: `);
  return raw || defaultVal;
}

async function promptYesNo(rl, label, defaultVal = true) {
  const hint = defaultVal ? 'Y/n' : 'y/N';
  const raw = (await ask(rl, `  ${chalk.cyan('→')} ${label} [${hint}]: `)).toLowerCase();
  if (!raw) return defaultVal;
  return raw === 'y' || raw === 'yes';
}

async function runInteractiveUpload() {
  console.log(BANNER);

  const remotes = getRcloneRemotes();
  if (remotes.length === 0) {
    console.log(chalk.red('  ✗ No cloud storage configured in rclone.'));
    console.log(chalk.yellow("    Please run 'rclone config' first to connect your Google Drive.\n"));
    process.exit(1);
  }

  const rl = createRl();

  try {
    // ─── Step 1: Remote Selection (auto if only 1 configured) ─────────────────
    let remote;
    let provider;

    if (remotes.length === 1) {
      remote = remotes[0];
      provider = remote.toLowerCase().includes('gdrive') ? 'gdrive' : 'generic';
      const remoteDisplay = remote.toLowerCase().includes('gdrive') ? 'Google Drive' : remote.replace(/:$/, '');
      console.log(`  ${chalk.green('✓ Cloud Target :')} ${chalk.white(`${remoteDisplay} (${remote})`)}\n`);
    } else {
      const options = remotes.map((r) => ({
        label: r,
        value: r.toLowerCase().includes('gdrive') ? 'gdrive' : 'generic',
        remote: r,
      }));
      const chosen = await promptChoice(rl, 'Select Cloud Storage', options);
      remote = chosen.remote;
      provider = chosen.value;
      console.log();
    }

    // ─── Step 2: Source File or Folder ───────────────────────────────────────
    let source = '';
    while (true) {
      const raw = await ask(rl, `  ${chalk.cyan('→')} Enter file or folder path: `);
      if (!raw) {
        console.log(chalk.red('    Path cannot be empty'));
        continue;
      }
      source = raw.replace(/^['"]|['"]$/g, '');
      if (fs.existsSync(source)) {
        if (fs.statSync(source).isDirectory()) {
          const files = fs.readdirSync(source);
          console.log(chalk.green(`    ✓ Folder verified — ${files.length} item(s)\n`));
        } else {
          const size = fs.statSync(source).size;
          console.log(chalk.green(`    ✓ File verified — ${formatSize(size)}\n`));
        }
        break;
      } else {
        console.log(chalk.red(`    ✗ File or folder not found: ${source}`));
        console.log(chalk.yellow('      Tip: You can drag & drop the file directly into this terminal window.'));
      }
    }

    // ─── Step 3: Destination Folder on Cloud ──────────────────────────────────
    const destInputRaw = await ask(rl, `  ${chalk.cyan('→')} Enter folder name on cloud ${chalk.gray('[Backup]')}: `);
    const destInput = destInputRaw.replace(/^['"]|['"]$/g, '') || 'Backup';

    const destination = await normalizeDestination(destInput, remote);
    console.log(`  ${chalk.green('✓ Destination   :')} ${chalk.white(destination)}\n`);

    // ─── Step 4: Simple Confirmation ──────────────────────────────────────────
    const confirm = (await ask(rl, `  ${chalk.cyan('→')} Start transfer now? ${chalk.gray('[Y/n]')}: `)).toLowerCase();
    rl.close();

    if (confirm === 'n' || confirm === 'no') {
      console.log(`\n  ${chalk.yellow('Transfer cancelled.\n')}`);
      process.exit(0);
    }

    // By default, do NOT compress — send original file as-is!
    const config = new UploadConfig({
      provider,
      compression: false,
      dedup: true,
    });
    const quota = new QuotaTracker(provider);

    console.log();
    let success;
    if (fs.existsSync(source) && fs.statSync(source).isDirectory()) {
      success = await uploadFolder(source, destination, config, quota);
    } else {
      success = await uploadFile(source, destination, config, quota);
    }

    process.exit(success ? 0 : 1);
  } catch (err) {
    rl.close();
    throw err;
  }
}

const program = new Command();

program
  .name('cloudboost')
  .version('3.0.0')
  .description('⚡ cloud-boost — supercharge your cloud uploads');

// ─── upload ──────────────────────────────────────────────────────────────────
program
  .command('upload')
  .description('Upload FILE or FOLDER to cloud DESTINATION')
  .argument('[source]', 'Local file or folder path')
  .argument('[destination]', 'Cloud destination (e.g. gdrive:/backup/)')
  .option('-t, --threads <n>', 'Upload threads (auto-tuned)', parseInt)
  .option('--no-compress', 'Skip compression entirely')
  .option('--no-dedup', 'Disable deduplication')
  .option('--max-bw <size>', 'Bandwidth limit e.g. 50MB')
  .option('--max-transfer <size>', 'Max bytes to upload e.g. 700GB (quota safety)')
  .option('-p, --provider <name>', 'Cloud provider', 'gdrive')
  .option('-v, --verbose', 'Verbose output', false)
  .action(async (source, destination, opts) => {
    // If no args provided, enter interactive wizard mode!
    if (!source || !destination) {
      await runInteractiveUpload();
      return;
    }

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
      compression: opts.compress !== false,
      dedup: opts.dedup !== false,
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
      remotes.forEach((r) => console.log(`    · ${r}`));
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
      remotes.forEach((r) => console.log(`  ✓ ${r}`));
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
