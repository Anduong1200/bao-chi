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

## 📁 Project Structure

```
crawl/
├── gui.py           # Triage UI (Stream/Reading Box/Archive)
├── main.py          # FlashNewsHunter orchestrator
├── archiver.py      # Auto-capture on URL detection
├── scanner.py       # RSS/Sitemap scanner
├── parser.py        # HTML parser
├── storage.py       # SQLite with triage status
├── config.yaml      # Sources configuration
├── config.py        # Config loader
├── alerter.py       # Telegram alerts
├── worker.py        # Worker pool
└── data/
    └── articles.db  # SQLite database
```

---

## 🎯 Core Features

### 1. Capture First
- Scan sources every 3-5 seconds
- IMMEDIATELY fetch + save HTML when new URL detected
- No waiting for user interaction

### 2. Triage Workflow (3 Tabs)

| Tab | Status | Actions |
|-----|--------|---------|
| ⚡ **Stream** | `new` (0) | [Pick] → Reading Box |
| 📖 **Reading Box** | `picked` (1) | [Save] / [Discard] |
| 📁 **Archive** | `archived` (2) | Export, Search |

### 3. Link Status Tracking
- 🟢 Live - Link còn sống
- 🔴 Dead - Link đã chết (vẫn đọc được từ cache)

### 4. Image Tracking
- Mỗi ảnh có ID riêng gắn với `article_id`
- Có thể tải ảnh về local sau

### 5. DB Control
- **Export Full DB** - Backup toàn bộ
- **Import DB** - Merge hoặc Replace

---

## ⚙️ Configuration

Edit `config.yaml` to add/modify sources:

```yaml
sources:
  - name: "ThanhNien_TrangChu"
    url: "https://thanhnien.vn/rss/home.rss"
    type: rss
    site_code: TNO
    enabled: true
    frequency: 60
```

---

## 📊 Database Schema

**Articles Table:**
```sql
articles (
    id TEXT PRIMARY KEY,
    source_name TEXT,
    url TEXT UNIQUE,
    title TEXT,
    content_html TEXT,
    status INTEGER,      -- 0=new, 1=picked, 2=archived, -1=discarded
    link_alive INTEGER,  -- 1=alive, 0=dead
    crawled_at TEXT
)
```

**Images Table:**
```sql
images (
    id TEXT PRIMARY KEY,
    article_id TEXT,     -- Foreign key
    url TEXT,
    local_path TEXT,
    downloaded INTEGER
)
```

---

## 🔧 API Reference

```python
from storage import get_storage

storage = get_storage()

# Triage
stream = storage.get_stream()           # Get new articles
storage.pick_article(article_id)        # Move to Reading Box
storage.archive_article(article_id)     # Save permanently
storage.discard_article(article_id)     # Throw away

# Images
storage.save_image(article_id, img_url)
images = storage.get_article_images(article_id)

# Backup
storage.export_full_db("backup.json")
storage.import_db("backup.json", merge=True)
```

---

## 📋 Workflow

```
1. Scanner detects new URL
2. Archiver IMMEDIATELY fetches HTML
3. Saved to DB with status=new
4. Appears in Stream tab
5. User clicks [Pick] → Reading Box
6. User reads from CACHE (works even if link is dead!)
7. User clicks [Save] → Archive
```

---

## 📝 License

MIT License
