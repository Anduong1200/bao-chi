# Flash News Hunter 📰⚡

**"Capture First, Review Later"** - Bắt tin nhanh trước khi bị xóa.

## Vấn đề
Báo chí hiện đại thường đăng bài "thăm dò" rồi xóa sau 5-10 phút. Tool thông thường không bắt được vì khi click vào thì link đã chết (404).

## Giải pháp
Hệ thống tự động tải HTML **ngay lập tức** khi phát hiện URL mới, lưu offline để đọc sau.

---

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run GUI
python gui.py

# Or run capture loop only (headless)
python main.py
```

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| **Capture First** | Tải HTML ngay khi phát hiện URL (3-5s interval) |
| **Concurrent Scanning** | Quét song song tất cả sources với `asyncio.gather` |
| **Physical Image Download** | Tải ảnh về `data/images/{article_id}/` |
| **Link Status** | � Live / 🔴 Dead (vẫn đọc được từ cache) |
| **Triage Workflow** | Stream → Reading Box → Archive |
| **FTS5 Search** | Full-text search siêu nhanh |
| **Hot Reload** | Sửa config.yaml không cần restart |
| **WAL Mode** | GUI + Crawler chạy song song không bị lock |
| **Proxy Rotation** | Chống bị chặn IP |
| **Auto Cleanup** | Tự động xóa bài discarded sau 7 ngày |

---

## �📁 Project Structure

```
crawl/
├── gui.py           # Triage UI (Stream/Reading Box/Archive)
├── main.py          # FlashNewsHunter orchestrator
├── archiver.py      # Auto-capture + image download
├── scanner.py       # RSS/Sitemap scanner
├── parser.py        # HTML parser
├── storage.py       # SQLite + FTS5 + WAL
├── config.yaml      # Sources + proxy + cleanup config
├── config.py        # Config loader with hot reload
├── alerter.py       # Telegram alerts
├── worker.py        # Worker pool (optional)
└── data/
    ├── articles.db  # SQLite database
    └── images/      # Downloaded images
```

---

## 🎯 Triage Workflow

```
┌─────────────────────────────────────────────────────┐
│  ⚡ THE STREAM (Tin mới đổ về)                      │
├─────────────────────────────────────────────────────┤
│ 🟢 09:01  ThanhNien  Vụ án XYZ...     [Pick]        │
│ 🔴 09:00  TuoiTre    Lãnh đạo từ...   [Pick]        │
└─────────────────────────────────────────────────────┘
                      │ Click [Pick]
                      ▼
┌─────────────────────────────────────────────────────┐
│  � READING BOX (Đọc từ cache offline)              │
├─────────────────────────────────────────────────────┤
│  Nội dung bài viết (dù link gốc đã chết)            │
│                                                     │
│         [� Save]              [� Discard]          │
└─────────────────────────────────────────────────────┘
                      │ Click [Save]
                      ▼
┌─────────────────────────────────────────────────────┐
│  📁 ARCHIVE (Kho lưu trữ)                           │
│  Export: .db (SQLite) hoặc .json                    │
└─────────────────────────────────────────────────────┘
```

---

## ⚙️ Configuration

### Sources (`config.yaml`)
```yaml
sources:
  - name: "ThanhNien_TrangChu"
    url: "https://thanhnien.vn/rss/home.rss"
    type: rss
    site_code: TNO
    enabled: true
    frequency: 5
```

### Proxy (Anti-blocking)
```yaml
proxy:
  enabled: true
  rotate: true
  list:
    - "http://user:pass@proxy1.com:8080"
    - "socks5://proxy2.com:1080"
```

### Auto Cleanup
```yaml
cleanup:
  enabled: true
  discard_after_days: 7
  run_on_start: false
```

---

## 💾 Export / Import

| Format | Use Case |
|--------|----------|
| `.db` | Full backup (instant copy, giữ FTS5 + indexes) |
| `.json` | Chỉ articles đã archive (portable) |

```python
from storage import get_storage
storage = get_storage()

# Export
storage.export_full_db("backup.db")      # SQLite copy
storage.export_json("archive.json")      # JSON

# Import
storage.import_db("backup.db", merge=True)   # Merge
storage.import_db("backup.db", merge=False)  # Replace
```

---

## 🔧 Performance

- **Concurrent scanning**: 10 sources × 2s = ~2s total (not 20s)
- **WAL mode**: GUI + Crawler đọc/ghi song song
- **FTS5**: Search 100k articles trong milliseconds
- **Background image download**: Không block main loop

---

## � API Reference

```python
from storage import get_storage

storage = get_storage()

# Triage
storage.get_stream()                    # Tin mới
storage.pick_article(id)                # → Reading Box
storage.archive_article(id)             # → Archive
storage.discard_article(id)             # → Trash

# Search (FTS5)
storage.search_articles("keyword")

# Cleanup
storage.auto_prune(days=7)              # Xóa discarded cũ
```

---

## �️ Stability Features

- **Hot Reload**: Sửa `config.yaml` → Tool tự reload scanners
- **WAL Mode**: Không bị "database is locked"
- **Error Recovery**: Checkpoint cho mỗi source
- **Graceful Shutdown**: Ctrl+C an toàn
