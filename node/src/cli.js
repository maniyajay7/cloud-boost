#!/usr/bin/env node
/**
 * cloud-boost CLI (Node.js)
 */

'use strict';

const { Command } = require('commander');
const { upload } = require('./index');
const { execSync } = require('child_process');

const BANNER = `
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST
  ⚡ Supercharge your cloud uploads
`;

const program = new Command();

program
  .name('cloudboost')
  .description('⚡ cloud-boost — fast parallel cloud uploads')
  .version('1.0.0');

program
  .command('upload <filepath> <destination>')
  .description('Upload a file or folder with parallel chunked upload')
  .option('-t, --threads <number>', 'Number of parallel threads', '6')
  .option('-c, --chunk-size <mb>', 'Chunk size in MB', '5')
  .option('--no-compress', 'Disable compression')
  .action(async (filepath, destination, options) => {
    console.log(BANNER);
    await upload({
      filepath,
      destination,
      threads: parseInt(options.threads),
      chunkSizeMb: parseInt(options.chunkSize),
      compress: options.compress !== false,
    });
  });

program
  .command('resume <filepath> <destination>')
  .description('Resume an interrupted upload')
  .action(async (filepath, destination) => {
    console.log(BANNER);
    console.log('→ Resuming upload:', filepath, '\n');
    await upload({ filepath, destination });
  });

program
  .command('stats')
  .description('Show rclone config and cloud connection info')
  .action(() => {
    console.log(BANNER);
    console.log('→ rclone remotes configured:\n');
    try {
      const output = execSync('rclone listremotes').toString().trim();
      output.split('\n').forEach(r => console.log(`  ✓ ${r}`));
    } catch {
      console.error('  ✗ rclone not found or not configured.');
    }
    console.log();
  });

program.parse(process.argv);
