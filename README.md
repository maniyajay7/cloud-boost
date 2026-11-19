# ⚡ cloud-boost v3

**Supercharge your cloud uploads.** Entropy-aware compression, quota-safe, auto-tuned rclone pipeline for uploading massive files (100GB–250GB+) to Google Drive.

Built for game folders, ISOs, backups — anything huge that needs to get to the cloud fast and reliably.

---

## What This Does

cloud-boost sits between you and Google Drive. Instead of dragging a 200GB game folder into Google Drive (which will choke, timeout, and lose progress), you run one command:

```bash
cloudboost upload "D:\Games\Elden Ring" gdrive:/games/
```

**What happens under the hood:**
1. **Scans** every file for compressibility (magic bytes + entropy analysis)
2. **Skips** compression on already-compressed files (.zip, .pak, .mp4, etc.)
3. **Compresses** only what benefits from it (saves bandwidth + time)
4. **Uploads** via rclone with optimized flags (auto-tuned chunk sizes, transfer counts, rate limits)
5. **Tracks** Google Drive's 750GB/day quota so you don't get randomly blocked
6. **Resumes** interrupted uploads — no re-uploading what already finished

---

## Requirements

Before using cloud-boost, you need:

| Requirement | Why | Install |
|------------|-----|---------|
| **rclone** | The actual upload engine | [rclone.org/install](https://rclone.org/install/) |
| **rclone Google Drive remote** | So rclone knows your Drive account | `rclone config` → follow prompts |
| **Node.js 18+** (for Node CLI) | Runtime | [nodejs.org](https://nodejs.org/) |
| **Python 3.8+** (for Python CLI) | Runtime | [python.org](https://python.org/) |

> You only need **one** of Node.js or Python — pick whichever you prefer.

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/maniyajay7/cloud-boost.git
cd cloud-boost
```

### 2a. Node.js Setup

```bash
cd node
npm install
```

To make `cloudboost` available as a global command:
```bash
npm link
```

Now you can run `cloudboost` from anywhere in your terminal.

### 2b. Python Setup

```bash
cd python
pip install -e .
```

For faster compression (5-10x speed boost):
```bash
pip install -e ".[fast]"
```

This installs `zstandard` (zstd) — without it, cloud-boost falls back to gzip which is slower but still works.

---

## Quick Start

### Step 1: Verify setup

```bash
cloudboost doctor
```

This checks:
- ✓ rclone installed
- ✓ Google Drive remote configured
- ✓ Custom OAuth Client ID (optional but 5-10x faster)
- ✓ Compression engine available

### Step 2: Upload a file

```bash
cloudboost upload myfile.zip gdrive:/backup/
```

### Step 3: Upload a folder (the real use case)

```bash
cloudboost upload "D:\Games\Cyberpunk 2077" gdrive:/games/
```

That's it. cloud-boost handles everything automatically.

---

## Commands

| Command | What it does |
|---------|-------------|
| `cloudboost upload <source> <dest>` | Upload file or folder to cloud |
| `cloudboost resume` | Show and resume interrupted uploads |
| `cloudboost doctor` | Diagnose your setup |
| `cloudboost setup` | Guide to create custom OAuth Client ID (5-10x faster) |
| `cloudboost stats` | Show configured remotes and compressor |
| `cloudboost init` | Initialize config at `~/.cloudboost/` |

### Upload Options

```bash
cloudboost upload <source> <destination> [options]

Options:
  -t, --threads <n>        Upload thread count (auto-tuned by default)
  --no-compress            Skip compression entirely
  --no-dedup               Don't skip already-uploaded files
  --max-bw <size>          Bandwidth limit (e.g. "50MB")
  --max-transfer <size>    Max bytes to upload (e.g. "700GB" for quota safety)
  -p, --provider <name>    Cloud provider (default: "gdrive")
  -v, --verbose            Verbose output
```

### Examples

```bash
# Upload a single file
cloudboost upload backup.tar.gz gdrive:/backups/

# Upload a 200GB game folder
cloudboost upload "D:\Games\GTA V" gdrive:/games/

# Upload with bandwidth limit (don't hog internet)
cloudboost upload ./data gdrive:/data/ --max-bw 50MB

# Upload with quota safety (stop before hitting 750GB daily limit)
cloudboost upload ./huge-backup gdrive:/backup/ --max-transfer 700GB

# Skip compression for already-compressed archives
cloudboost upload ./zips gdrive:/archives/ --no-compress

# See what's interrupted and can be resumed
cloudboost resume
```

---

## How It Works

### Architecture

```
┌──────────────────────────────────────────────────────┐
│                   cloud-boost CLI                     │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─────────┐   ┌────────────┐   ┌────────────────┐  │
│  │ Entropy  │──▶│ Compressor │──▶│   Transport    │  │
│  │ Analyzer │   │ (zstd/gz)  │   │ (rclone exec)  │  │
│  └─────────┘   └────────────┘   └────────────────┘  │
│       │                               │              │
│  ┌─────────┐                    ┌─────────────┐      │
│  │ Config  │                    │  Manifest   │      │
│  │AutoTune │                    │  (resume)   │      │
│  └─────────┘                    └─────────────┘      │
│                                       │              │
│                                 ┌─────────────┐      │
│                                 │   Quota     │      │
│                                 │  Tracker    │      │
│                                 └─────────────┘      │
└──────────────────────────────────────────────────────┘
                        │
                   rclone copyto / copy
                        │
                  ┌─────────────┐
                  │ Google Drive│
                  │   (API)     │
                  └─────────────┘
```

### Upload Strategy (Two-Tier)

| File Size | Strategy |
|-----------|----------|
| **< 50MB** | Direct rclone upload — no processing overhead |
| **50MB+** | Entropy analysis → smart compression → optimized rclone upload |
| **Folder** | Scan all files → compress compressible ones into staging dir → single `rclone copy` |

### Entropy Detection (Why We Don't Compress Everything)

Most game files (.pak, .zip, .mp4, .wem) are **already compressed**. Trying to gzip a .pak file makes it **bigger**, wastes CPU, and wastes time.

cloud-boost uses a 3-phase detection pipeline:

1. **Magic bytes** — Read first 16 bytes, match against 25+ known compressed format signatures (O(1))
2. **Extension check** — Match against 50+ known compressed extensions (O(1))
3. **Shannon entropy sampling** — Sample head + middle + end of file, compute entropy score:
   - Entropy > 7.2 → Already compressed → **skip**
   - Entropy < 6.0 → Highly compressible → **compress aggressively**
   - 6.0–7.2 → Maybe compressible → **compress with fast level, verify 5% savings**

### Google Drive Quota Awareness

Google Drive has a **750GB/day upload limit** per user. cloud-boost tracks cumulative uploads and:
- **Warns** at 93% usage (697GB)
- **Blocks** at 100% with a clear message instead of letting you hit a cryptic 403

---

## Speed Optimization: Custom OAuth Client ID

The **single biggest speed improvement** you can make. By default, rclone uses a shared client ID that thousands of people share — Google throttles it heavily.

Run `cloudboost setup` to get a step-by-step guide for creating your own. Result: **5-10x faster uploads**.

---

## Configuration

Config is stored at `~/.cloudboost/config.json`. Created automatically on first run.

```json
{
  "threads": 6,
  "chunkSize": "8MB",
  "compression": true,
  "compressionLevel": 6,
  "resume": true,
  "provider": "gdrive",
  "maxBandwidth": 0,
  "maxRetries": 3,
  "checksumVerify": true,
  "dedup": true,
  "verbose": false
}
```

The auto-tune engine overrides these defaults based on what you're uploading:
- Small files (<100MB): 4 transfers, 64M chunks
- Medium files (<1GB): 6 transfers, 128M chunks
- Large files (<10GB): 8 transfers, 128M chunks
- Huge files (10GB+): 8 transfers, 256M chunks, lower compression

---

## If You Already Have rclone Mounted (like Z: drive)

If you're already using `rclone mount gdrive: Z:` — you're using rclone to **mount** Google Drive as a local drive. That works for browsing files, but it's **slow for large uploads** because every file write goes through FUSE.

cloud-boost uses `rclone copy` / `rclone copyto` directly — this is **much faster** because:
- No FUSE overhead
- Optimized chunk sizes (128M-256M vs default 8M)
- Parallel transfers (8 simultaneous)
- Rate limit awareness (no random 403s)

**You can use both.** Keep your Z: mount for browsing, use cloud-boost for uploading big stuff.

---

## Project Structure

```
cloud-boost/
├── node/                      # Node.js implementation
│   ├── package.json
│   └── src/
│       ├── cli.js             # CLI commands (upload, resume, doctor, setup, stats, init)
│       ├── config.js          # Configuration + auto-tune engine
│       ├── entropy.js         # Magic bytes + Shannon entropy detection
│       ├── compressor.js      # zstd/gzip streaming compression
│       ├── manifest.js        # File-level upload tracking + resume
│       ├── transport.js       # rclone wrapper + quota tracker
│       ├── index.js           # Core uploader (two-tier strategy)
│       └── setup-wizard.js    # OAuth Client ID setup guide
├── python/                    # Python implementation
│   ├── setup.py
│   ├── requirements.txt
│   └── cloudboost/
│       ├── cli.py             # CLI commands
│       ├── config.py          # Configuration + auto-tune
│       ├── entropy.py         # Magic bytes + Shannon entropy
│       ├── compressor.py      # zstd/gzip compression
│       ├── manifest.py        # Upload tracking + resume
│       ├── transport.py       # rclone wrapper + quota tracker
│       ├── uploader.py        # Core uploader
│       ├── setup_wizard.py    # OAuth setup guide
│       └── logger.py          # Structured JSON logging
├── docs/                      # Documentation
├── README.md                  # This file
└── .gitignore
```

Both implementations (Node.js and Python) are **functionally identical** — same architecture, same features, same rclone flags. Use whichever runtime you prefer.

---

## License

MIT — do whatever you want with it.
