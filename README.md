# Flash News Hunter 📰⚡

**"Capture First, Review Later"** - Bắt tin nhanh trước khi bị xóa.

## Vấn đề
Báo chí hiện đại thường đăng bài "thăm dò" rồi xóa sau 5-10 phút. Tool thông thường không bắt được vì khi click vào thì link đã chết (404).

## Giải pháp
Hệ thống tự động tải HTML **ngay lập tức** khi phát hiện URL mới, lưu offline để đọc sau.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python gui.py
```

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| **Capture First** | Tải HTML ngay khi phát hiện URL |
| **Concurrent Scanning** | Quét song song với `asyncio.gather` |
| **Image Download** | Tải ảnh về `data/images/{article_id}/` |
| **Link Status** | 🟢 Live / 🔴 Dead (vẫn đọc được từ cache) |
| **FTS5 Search** | Full-text search siêu nhanh |
| **Hot Reload** | Sửa config không cần restart |
| **WAL Mode** | GUI + Crawler không bị lock |
| **Proxy Rotation** | Chống bị chặn IP |
| **Auto Cleanup** | Xóa bài discarded sau 7 ngày |

---

## �️ Anti-Ban System

### Random Jitter
```python
# Tự động thêm 0.5-2s delay mỗi cycle
sleep_time = poll_interval + random.uniform(0.5, 2.0)
```

### Rate Limit Detection
```
[Archiver] ⚠️ RATE LIMITED (429): ...
```
Tự động dừng khi bị chặn.

### Ngưỡng an toàn

| Setup | Frequency | Risk |
|-------|-----------|------|
| IP cá nhân | 15-20s | ✅ Safe |
| Có Proxy | 5s | ✅ Safe |
| IP cá nhân + 5s | ⚠️ Bị ban | ❌ |

---

## 🎯 Triage Workflow

```
⚡ THE STREAM (Tin mới) → [Pick]
        ↓
📖 READING BOX (Cache) → [Save] / [Discard]
        ↓
📁 ARCHIVE (Export .db)
```

---

## ⚙️ Configuration

### Sources
```yaml
sources:
  - name: "ThanhNien_TrangChu"
    url: "https://thanhnien.vn/rss/home.rss"
    frequency: 15  # Khuyên dùng 15-20s nếu không có proxy
    enabled: true
```

### Proxy (Anti-blocking)
```yaml
proxy:
  enabled: true
  rotate: true
  list:
    - "http://user:pass@proxy.com:8080"
```

### Auto Cleanup
```yaml
cleanup:
  enabled: true
  discard_after_days: 7
```

---

## 💾 Export / Import

| Format | Description |
|--------|-------------|
| `.db` | SQLite copy (nhanh, giữ FTS5) |
| `.json` | Chỉ articles archived |

---

## 📁 Project Structure

```
crawl/
├── gui.py           # Triage UI
├── main.py          # Orchestrator + Hot Reload
├── archiver.py      # Capture + Image + Proxy
├── scanner.py       # RSS/Sitemap scanner
├── parser.py        # HTML parser
├── storage.py       # SQLite + FTS5 + WAL
├── config.yaml      # Configuration
└── data/
    ├── articles.db
    └── images/
```

---

## 🔧 API

```python
from storage import get_storage
storage = get_storage()

# Triage
storage.get_stream()
storage.pick_article(id)
storage.archive_article(id)

# Search (FTS5)
storage.search_articles("keyword")

# Cleanup
storage.auto_prune(days=7)

# Export
storage.export_full_db("backup.db")
```
