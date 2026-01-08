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
| **Historical Scan** | 🕰️ Quét bài cũ theo ngày (Pagination) |
| **Capture First** | Tải HTML ngay khi phát hiện URL |
| **Live Stream** | ⚡ Theo dõi tin mới realtime (No history load) |
| **Concurrent Scanning** | Quét song song với `asyncio.gather` |
| **Image Download** | Tải ảnh về `data/images/{article_id}/` |
| **Link Status** | 🟢 Live / 🔴 Dead (vẫn đọc được từ cache) |
| **FTS5 Search** | Full-text search siêu nhanh |
| **Hot Reload** | Sửa config không cần restart |
| **Anti-Blocking** | Proxy Rotation + Random Jitter |

---

## 🚦 Anti-Ban System

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

---

## 🎯 Workflows

### 1. The Stream (Live Triage)
Theo dõi tin tức mới nhất theo thời gian thực.
- **Filter**: Lọc theo nguồn (Source) hoặc tìm kiếm tiêu đề.
- **Pick**: Chọn bài viết quan trọng → Chuyển sang Reading Box.
- **Live Mode**: Không load lại lịch sử cũ, chỉ hiện tin mới.

### 2. Archive Hunter (Historical Scan)
Quét và lưu trữ bài viết từ quá khứ (Deep Scan).
1. Chọn nguồn (VD: Thanh Niên).
2. Chọn ngày cần quét (VD: 2024-01-01).
3. Bấm **Deep Scan** → Hệ thống tự động lùi trang (backtrack pagination) để tìm bài.

### 3. Reading Box (Review)
Nơi đọc và xử lý các bài đã chọn.
- **Read**: Đọc offline (Text + Ảnh).
- **Archive**: Lưu vĩnh viễn (status=2) + Export `.json`/`.db`.
- **Discard**: Xóa.

---

## ⚙️ Configuration

### Sources & Deep Scan
```yaml
sources:
  - name: "ThanhNien_TrangChu"
    url: "https://thanhnien.vn/rss/home.rss"
    deep_scan:
      base_url: "https://thanhnien.vn/thoi-su" # URL trang danh sách
      page_param: "p"       # ?p=1, ?p=2...
      date_css: ".box-category-time" # CSS lấy ngày
      date_format: "%d/%m/%Y"
```

### Proxy (Anti-blocking)
```yaml
proxy:
  enabled: true
  rotate: true
  list:
    - "http://user:pass@proxy.com:8080"
```

---

## 🔧 API & CLI

```python
from storage import get_storage
storage = get_storage()

# Triage & Management
storage.get_stream(limit=100)
storage.pick_article(id)
storage.archive_article(id)

# Search (FTS5)
storage.search_articles("keyword")

# Maintenance
storage.auto_prune(days=7)  # Xóa bài rác
storage.export_full_db("backup.db") # Backup toàn bộ
```
