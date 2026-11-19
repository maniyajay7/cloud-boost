# ⚡ cloud-boost

> Supercharge your cloud uploads. Parallel chunks, compression, resume support — works with Google Drive, S3, Azure, Dropbox and more via rclone.

---

## 🚀 Why cloud-boost?

Default cloud uploads are slow because they:
- Upload one chunk at a time (no parallelism)
- Send raw uncompressed files
- Restart from zero on failure
- Use slow single TCP connections

**cloud-boost fixes all of that.**

---

## 📦 Install

### Python CLI
```bash
pip install cloud-boost
```

### Node.js CLI
```bash
npm install -g cloud-boost
```

---

## ⚡ Usage

```bash
# Upload a single file
cloudboost upload myfile.zip gdrive:/backup/

# Upload a folder
cloudboost upload ./project gdrive:/projects/

# Custom threads & chunk size
cloudboost upload bigfile.iso gdrive:/iso/ --threads 8 --chunk-size 10MB

# Resume interrupted upload
cloudboost resume gdrive:/backup/myfile.zip

# Check upload speed stats
cloudboost stats
```

---

## 🔧 How It Works

```
Your File
   │
   ▼
Split into chunks (default 5MB each)
   │
   ▼
Compress each chunk (gzip)
   │
   ▼
Upload ALL chunks in PARALLEL (default 6 threads)
   │
   ▼
Cloud reassembles → Done ✅
```

---

## ⚙️ Configuration

```yaml
# ~/.cloudboost/config.yml
threads: 6
chunk_size: 5MB
compression: true
resume: true
provider: gdrive   # gdrive | s3 | azure | dropbox
```

---

## 🛡️ Features

| Feature | Status |
|---|---|
| Parallel chunk upload | ✅ |
| Auto compression (gzip) | ✅ |
| Resume on failure | ✅ |
| Progress bar | ✅ |
| Multi-cloud support | ✅ |
| rclone integration | ✅ |
| Speed stats | ✅ |

---

## 🌐 Supported Providers

- ☁️ Google Drive (via rclone)
- 🟠 AWS S3
- 🔵 Azure Blob Storage
- 📦 Dropbox
- 🌊 DigitalOcean Spaces
- 🟩 Supabase Storage

---

## 📄 License

MIT © Jay Maniya
