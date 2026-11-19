# ⚡ cloud-boost v2

> Production-grade cloud upload accelerator. Parallel chunks, streaming compression, SHA-256 integrity, resume support — handles 100GB+ files. Works with Google Drive, S3, Azure, Dropbox via rclone.

---

## 🚀 Why cloud-boost?

Default cloud uploads are painfully slow because they:
- Upload one chunk at a time (zero parallelism)
- Send raw uncompressed data
- Restart from zero on any failure
- Use single TCP connections
- No integrity verification

**cloud-boost fixes all of that.**

---

## ⚙️ Architecture

```
                          ┌──────────────┐
                          │  Source File  │
                          └──────┬───────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Auto-Tune Engine       │
                    │  (adapts to file size)  │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                   │
         < 50MB             50MB – 10GB           > 10GB
         Direct             6-8 threads          12 threads
         rclone             8MB chunks           32MB chunks
         upload             Level 6 gzip         Level 2 gzip
              │                  │                   │
              └──────────┬───────┘                   │
                         │                           │
                ┌────────▼─────────┐                 │
                │ Streaming Pipeline│◄────────────────┘
                │                  │
                │  Read chunk      │
                │  ↓ (in memory)   │
                │  Compress chunk  │
                │  ↓ (in memory)   │
                │  Write temp .gz  │
                │  ↓               │
                │  Upload rclone   │  × N threads parallel
                │  ↓               │
                │  Delete temp     │
                │  ↓               │
                │  Save checkpoint │
                └──────────────────┘
                         │
                ┌────────▼─────────┐
                │  SHA-256 Verify  │
                │  + Manifest Save │
                └──────────────────┘
```

---

## 📦 Install

### Python CLI
```bash
cd python
pip install -e .
```

### Node.js CLI
```bash
cd node
npm install
npm link
```

### Prerequisites
- **rclone** — [Install rclone](https://rclone.org/install/)
- Configure your remote: `rclone config`

---

## ⚡ Usage

```bash
# Upload a file (auto-tunes everything)
cloudboost upload myfile.zip gdrive:/backup/

# Upload a folder (recursive)
cloudboost upload ./project gdrive:/projects/

# Custom threads & chunk size
cloudboost upload big.iso gdrive:/iso/ --threads 8 --chunk-size 32MB

# Bandwidth limiting
cloudboost upload data.tar gdrive:/data/ --max-bw 50MB

# Resume interrupted upload
cloudboost resume big.iso gdrive:/iso/

# Check setup
cloudboost doctor

# Show remotes
cloudboost stats

# Initialize config
cloudboost init
```

---

## 🧠 Auto-Tune Engine

You don't need to configure anything. cloud-boost automatically adapts based on file size:

| File Size | Threads | Chunk Size | Compression | rclone Transfers |
|---|---|---|---|---|
| < 50 MB | Direct upload | — | — | — |
| < 100 MB | 4 | 4 MB | Level 6 | 8 |
| < 1 GB | 6 | 8 MB | Level 6 | 8 |
| < 10 GB | 8 | 16 MB | Level 4 | 12 |
| > 10 GB | 12 | 32 MB | Level 2 | 16 |

Override any setting via CLI flags — your values always win.

---

## 🔄 Resume System

Every chunk upload is checkpointed via a **manifest file** in `~/.cloudboost/checkpoints/`.

If your upload fails (network drop, power outage, Ctrl+C):
```bash
cloudboost resume myfile.zip gdrive:/backup/
# Picks up exactly where it left off — zero wasted bandwidth
```

The manifest tracks:
- Source file SHA-256 hash (detects file changes)
- Per-chunk hash (dedup — skips unchanged chunks)
- Upload status per chunk
- Compression metadata

---

## 🛡️ Features

| Feature | Description |
|---|---|
| **Streaming pipeline** | Never writes raw chunks to disk — only compressed temps |
| **Parallel upload** | 4-12 threads auto-tuned to file size |
| **gzip compression** | Level auto-tuned: fast (2) for huge files, balanced (6) for normal |
| **SHA-256 integrity** | Every chunk verified, source file hashed |
| **Resume on failure** | Manifest-based checkpoint system |
| **Deduplication** | Hash-based skip for unchanged chunks |
| **Auto-tuning** | Zero config needed — adapts to file size |
| **Exponential backoff** | Smart retry with 2^n delay |
| **Bandwidth throttle** | `--max-bw` flag to cap upload speed |
| **Sub-folder chunking** | Chunks stored in hex sub-dirs (256 max per folder) |
| **Small file fast path** | Files < 50MB skip chunking entirely |
| **Folder upload** | Recursive with per-file manifests |
| **Diagnostics** | `cloudboost doctor` checks everything |

---

## 🌐 Supported Providers (via rclone)

- ☁️ Google Drive
- 🟠 AWS S3
- 🔵 Azure Blob Storage
- 📦 Dropbox
- 🌊 DigitalOcean Spaces
- 🟩 Supabase Storage
- 🗂️ Any rclone-supported backend (40+ providers)

---

## 📁 Project Structure

```
cloud-boost/
├── README.md
├── .gitignore
├── docs/
├── python/
│   ├── setup.py
│   ├── requirements.txt
│   └── cloudboost/
│       ├── __init__.py
│       ├── config.py        ← auto-tune engine
│       ├── integrity.py     ← SHA-256, manifests, dedup
│       ├── transport.py     ← rclone subprocess, retries
│       ├── logger.py        ← structured JSON logging
│       ├── uploader.py      ← core streaming pipeline
│       └── cli.py           ← Click CLI
└── node/
    ├── package.json
    └── src/
        ├── config.js        ← auto-tune engine
        ├── integrity.js     ← SHA-256, manifests, dedup
        ├── transport.js     ← rclone subprocess, retries
        ├── index.js         ← core streaming pipeline
        └── cli.js           ← Commander CLI
```

---

## 📊 Performance Estimates

| Upload Speed | 1 GB (no boost) | 1 GB (cloud-boost) | 100 GB (cloud-boost) |
|---|---|---|---|
| 10 Mbps | ~14 min | ~3 min | ~5 hours |
| 50 Mbps | ~3 min | ~40 sec | ~1 hour |
| 100 Mbps | ~90 sec | ~20 sec | ~30 min |

*Actual results depend on file compressibility and cloud provider limits.*

---

## 📄 License

MIT © Jay Maniya
