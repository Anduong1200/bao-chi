"""
Storage Module - Flash News Hunter
SQLite with status-based triage workflow.
Status: 0=new, 1=picked, 2=archived, -1=discarded
"""

import sqlite3
import json
import aiohttp
import aiofiles
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager

from config import get_config, ensure_directories


# Status constants
STATUS_NEW = 0        # Fresh from scanner, untouched
STATUS_PICKED = 1     # In Reading Box
STATUS_ARCHIVED = 2   # Saved permanently
STATUS_DISCARDED = -1 # Thrown away


@dataclass
class Article:
    """Article with triage status."""
    id: str
    source: str
    source_name: str
    url: str
    title: str
    sapo: str
    author: str
    content_text: str
    content_html: str
    images: List[str]
    published_at: str
    crawled_at: str
    status: int = STATUS_NEW  # Triage status
    link_alive: bool = True   # Is original link still alive?
    category: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['images'] = json.dumps(self.images)
        return d
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'Article':
        data = dict(row)
        data['images'] = json.loads(data.get('images', '[]'))
        data['link_alive'] = bool(data.get('link_alive', 1))
        return cls(**data)


@dataclass
class ScanState:
    """Checkpoint for disaster recovery."""
    source_name: str
    last_article_id: str
    last_article_url: str
    last_scan_time: str
    articles_count: int


class Storage:
    """SQLite storage with triage workflow support."""
    
    def __init__(self):
        self.config = get_config()
        ensure_directories()
        self.db_path = self.config.storage.db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database with triage status support."""
        with self._get_connection() as conn:
            # Enable WAL mode for concurrent read/write (GUI + Crawler)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            
            conn.executescript('''
                -- Articles table with status
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    sapo TEXT,
                    author TEXT,
                    content_text TEXT,
                    content_html TEXT,
                    images TEXT,
                    published_at TEXT,
                    crawled_at TEXT NOT NULL,
                    status INTEGER DEFAULT 0,
                    link_alive INTEGER DEFAULT 1,
                    category TEXT
                );
                
                CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
                CREATE INDEX IF NOT EXISTS idx_articles_crawled ON articles(crawled_at DESC);
                CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_name);
                
                -- Seen URLs for deduplication
                CREATE TABLE IF NOT EXISTS seen_urls (
                    url TEXT PRIMARY KEY,
                    article_id TEXT,
                    source_name TEXT,
                    first_seen_at TEXT NOT NULL
                );
                
                -- Images table with article association
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY,
                    article_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    local_path TEXT,
                    downloaded INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (article_id) REFERENCES articles(id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_images_article ON images(article_id);
                
                -- Checkpoint for recovery
                CREATE TABLE IF NOT EXISTS scan_state (
                    source_name TEXT PRIMARY KEY,
                    last_article_id TEXT,
                    last_article_url TEXT,
                    last_scan_time TEXT,
                    articles_count INTEGER DEFAULT 0
                );
                
                -- Error log
                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    url TEXT,
                    timestamp TEXT
                );
            ''')
            
            # Add columns if not exist (migration)
            try:
                conn.execute("ALTER TABLE articles ADD COLUMN status INTEGER DEFAULT 0")
            except: pass
            try:
                conn.execute("ALTER TABLE articles ADD COLUMN link_alive INTEGER DEFAULT 1")
            except: pass
            
            # FTS5 Full-Text Search (fast search)
            try:
                conn.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                        title, 
                        sapo, 
                        content_text,
                        content=articles,
                        content_rowid=rowid
                    )
                ''')
            except: pass
            
            conn.commit()
        print(f"[Storage] Initialized: {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    # === TRIAGE WORKFLOW ===
    
    def get_stream(self, limit: int = 100) -> List[Article]:
        """Get new articles (status=0) for The Stream."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM articles 
                   WHERE status = ? 
                   ORDER BY crawled_at DESC LIMIT ?""",
                (STATUS_NEW, limit)
            )
            return [Article.from_row(row) for row in cursor.fetchall()]
    
    def get_picked(self) -> List[Article]:
        """Get picked articles (status=1) for Reading Box."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM articles 
                   WHERE status = ? 
                   ORDER BY crawled_at DESC""",
                (STATUS_PICKED,)
            )
            return [Article.from_row(row) for row in cursor.fetchall()]
    
    def get_archived(self, limit: int = 500) -> List[Article]:
        """Get archived articles (status=2)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM articles 
                   WHERE status = ? 
                   ORDER BY crawled_at DESC LIMIT ?""",
                (STATUS_ARCHIVED, limit)
            )
            return [Article.from_row(row) for row in cursor.fetchall()]
    
    def pick_article(self, article_id: str) -> bool:
        """Move article to Reading Box (status=1)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = ? WHERE id = ?",
                (STATUS_PICKED, article_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def archive_article(self, article_id: str) -> bool:
        """Save article permanently (status=2)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = ? WHERE id = ?",
                (STATUS_ARCHIVED, article_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def discard_article(self, article_id: str) -> bool:
        """Discard article (status=-1)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = ? WHERE id = ?",
                (STATUS_DISCARDED, article_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def unpick_article(self, article_id: str) -> bool:
        """Return article to stream (status=0)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = ? WHERE id = ?",
                (STATUS_NEW, article_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def update_link_status(self, url: str, alive: bool) -> bool:
        """Update link alive status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE articles SET link_alive = ? WHERE url = ?",
                (1 if alive else 0, url)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    # === DEDUPLICATION ===
    
    def is_seen(self, url: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM seen_urls WHERE url = ? LIMIT 1", (url,)
            )
            return cursor.fetchone() is not None
    
    def mark_seen(self, url: str, article_id: str, source_name: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_urls VALUES (?, ?, ?, ?)",
                (url, article_id, source_name, datetime.utcnow().isoformat())
            )
            conn.commit()
    
    def filter_new_urls(self, urls: List[str]) -> List[str]:
        if not urls:
            return []
        with self._get_connection() as conn:
            placeholders = ','.join('?' * len(urls))
            cursor = conn.execute(
                f"SELECT url FROM seen_urls WHERE url IN ({placeholders})", urls
            )
            seen = {row['url'] for row in cursor.fetchall()}
        return [url for url in urls if url not in seen]
    
    # === ARTICLE CRUD ===
    
    def save_article(self, article: Article) -> bool:
        """Save article with status=new (0)."""
        try:
            with self._get_connection() as conn:
                data = article.to_dict()
                columns = ', '.join(data.keys())
                placeholders = ', '.join('?' * len(data))
                
                conn.execute(
                    f"INSERT OR REPLACE INTO articles ({columns}) VALUES ({placeholders})",
                    list(data.values())
                )
                conn.execute(
                    "INSERT OR IGNORE INTO seen_urls VALUES (?, ?, ?, ?)",
                    (article.url, article.id, article.source_name, article.crawled_at)
                )
                conn.commit()
            return True
        except Exception as e:
            print(f"[Storage] Save error: {e}")
            return False
    
    def get_article(self, article_id: str) -> Optional[Article]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM articles WHERE id = ?", (article_id,)
            )
            row = cursor.fetchone()
            return Article.from_row(row) if row else None
    
    def get_article_by_url(self, url: str) -> Optional[Article]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM articles WHERE url = ?", (url,)
            )
            row = cursor.fetchone()
            return Article.from_row(row) if row else None
    
    def get_all_articles(self) -> List[Article]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM articles ORDER BY crawled_at DESC"
            )
            return [Article.from_row(row) for row in cursor.fetchall()]
    
    def search_articles(self, keyword: str, limit: int = 100) -> List[Article]:
        """Fast full-text search using FTS5."""
        with self._get_connection() as conn:
            try:
                # Try FTS5 first (much faster)
                cursor = conn.execute(
                    """SELECT articles.* FROM articles
                       JOIN articles_fts ON articles.rowid = articles_fts.rowid
                       WHERE articles_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (keyword, limit)
                )
            except:
                # Fallback to LIKE if FTS5 not available
                cursor = conn.execute(
                    """SELECT * FROM articles 
                       WHERE title LIKE ? OR content_text LIKE ?
                       ORDER BY crawled_at DESC LIMIT ?""",
                    (f'%{keyword}%', f'%{keyword}%', limit)
                )
            return [Article.from_row(row) for row in cursor.fetchall()]
    
    # === CHECKPOINT ===
    
    def get_checkpoint(self, source_name: str) -> Optional[ScanState]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scan_state WHERE source_name = ?", (source_name,)
            )
            row = cursor.fetchone()
            if row:
                return ScanState(**dict(row))
        return None
    
    def update_checkpoint(self, source_name: str, article_id: str, url: str):
        with self._get_connection() as conn:
            conn.execute('''
                INSERT INTO scan_state VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_article_id = excluded.last_article_id,
                    last_article_url = excluded.last_article_url,
                    last_scan_time = excluded.last_scan_time,
                    articles_count = articles_count + 1
            ''', (source_name, article_id, url, datetime.utcnow().isoformat()))
            conn.commit()
    
    # === STATS ===
    
    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            new = conn.execute("SELECT COUNT(*) FROM articles WHERE status=0").fetchone()[0]
            picked = conn.execute("SELECT COUNT(*) FROM articles WHERE status=1").fetchone()[0]
            archived = conn.execute("SELECT COUNT(*) FROM articles WHERE status=2").fetchone()[0]
            discarded = conn.execute("SELECT COUNT(*) FROM articles WHERE status=-1").fetchone()[0]
            dead_links = conn.execute("SELECT COUNT(*) FROM articles WHERE link_alive=0").fetchone()[0]
            
            today = datetime.utcnow().strftime('%Y-%m-%d')
            today_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE crawled_at LIKE ?",
                (f'{today}%',)
            ).fetchone()[0]
        
        return {
            'total': total,
            'new': new,
            'picked': picked,
            'archived': archived,
            'discarded': discarded,
            'dead_links': dead_links,
            'today': today_count,
            'db_size_mb': round(self.db_path.stat().st_size / 1024 / 1024, 2) if self.db_path.exists() else 0
        }
    
    # === EXPORT ===
    
    def export_json(self, path: str, status: Optional[int] = STATUS_ARCHIVED):
        """Export articles (default: archived only)."""
        if status is not None:
            articles = self.get_archived() if status == STATUS_ARCHIVED else self.get_stream()
        else:
            articles = self.get_all_articles()
        
        data = [asdict(a) for a in articles]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Storage] Exported {len(articles)} articles to {path}")
    
    def export_html(self, article_id: str, path: str):
        """Export single article to HTML."""
        article = self.get_article(article_id)
        if not article:
            return
        
        import re
        clean_content = re.sub(r'<img[^>]*>', '', article.content_html)
        
        html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <title>{article.title}</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #e94560; }}
        .meta {{ color: #666; margin-bottom: 20px; }}
        .sapo {{ font-style: italic; margin-bottom: 20px; }}
        .status-dead {{ color: #e74c3c; }}
        .status-alive {{ color: #27ae60; }}
    </style>
</head>
<body>
    <h1>{article.title}</h1>
    <div class="meta">
        <b>ID:</b> {article.id}<br>
        <b>Source:</b> {article.source_name}<br>
        <b>Author:</b> {article.author}<br>
        <b>Published:</b> {article.published_at}<br>
        <b>Captured:</b> {article.crawled_at}<br>
        <b>Link Status:</b> <span class="status-{'alive' if article.link_alive else 'dead'}">
            {'🟢 Alive' if article.link_alive else '🔴 Dead'}
        </span>
    </div>
    <div class="sapo">{article.sapo}</div>
    <hr>
    <div>{clean_content}</div>
    <hr>
    <p><a href="{article.url}">{article.url}</a></p>
</body>
</html>"""
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
    
    # === IMAGE MANAGEMENT ===
    
    def save_image(self, article_id: str, image_url: str, local_path: str = None) -> str:
        """Save image record linked to article."""
        import hashlib
        image_id = hashlib.md5(f"{article_id}_{image_url}".encode()).hexdigest()[:16]
        
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO images (id, article_id, url, local_path, downloaded, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                image_id, article_id, image_url, 
                local_path, 1 if local_path else 0,
                datetime.utcnow().isoformat()
            ))
            conn.commit()
        return image_id
    
    def get_article_images(self, article_id: str) -> List[dict]:
        """Get all images for an article."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM images WHERE article_id = ?", (article_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_image(self, image_id: str) -> Optional[dict]:
        """Get single image by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM images WHERE id = ?", (image_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def mark_image_downloaded(self, image_id: str, local_path: str):
        """Mark image as downloaded with local path."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE images SET downloaded = 1, local_path = ? WHERE id = ?",
                (local_path, image_id)
            )
            conn.commit()
    
    def get_pending_images(self, limit: int = 100) -> List[dict]:
        """Get images that haven't been downloaded yet."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM images WHERE downloaded = 0 LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    # === IMPORT / EXPORT DATABASE ===
    
    def export_full_db(self, output_path: str):
        """Export entire database to SQLite backup file."""
        import shutil
        from pathlib import Path
        
        # Ensure .db extension
        if not output_path.endswith('.db'):
            output_path = output_path.replace('.json', '.db')
            if not output_path.endswith('.db'):
                output_path += '.db'
        
        # Copy the DB file directly (fastest and preserves everything)
        shutil.copy2(str(self.db_path), output_path)
        
        # Get stats for reporting
        with self._get_connection() as conn:
            articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        
        print(f"[Storage] DB exported: {articles} articles, {images} images -> {output_path}")
        return {'articles': articles, 'images': images, 'path': output_path}
    
    def export_json(self, path: str, status: Optional[int] = STATUS_ARCHIVED):
        """Export filtered articles to JSON."""
        if status is not None:
            articles = self.get_archived() if status == STATUS_ARCHIVED else self.get_stream()
        else:
            articles = self.get_all_articles()
        
        data = [asdict(a) for a in articles]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Storage] Exported {len(articles)} articles to {path}")
    
    def import_db(self, input_path: str, merge: bool = True):
        """
        Import database from backup file (.db or .json).
        
        Args:
            input_path: Path to backup file
            merge: If True, merge with existing. If False, replace all.
        """
        from pathlib import Path
        import shutil
        
        # Check if it's a SQLite DB file
        if input_path.endswith('.db'):
            if not merge:
                # Replace: just copy the file
                shutil.copy2(input_path, str(self.db_path))
                print(f"[Storage] DB replaced from {input_path}")
                return {'articles': '(replaced)', 'images': '(replaced)'}
            else:
                # Merge: attach and copy data
                with self._get_connection() as conn:
                    conn.execute(f"ATTACH DATABASE '{input_path}' AS import_db")
                    
                    # Import articles
                    conn.execute("""
                        INSERT OR REPLACE INTO articles 
                        SELECT * FROM import_db.articles
                    """)
                    
                    # Import images
                    conn.execute("""
                        INSERT OR REPLACE INTO images 
                        SELECT * FROM import_db.images
                    """)
                    
                    conn.commit()  # Commit transaction BEFORE detach
                    conn.execute("DETACH DATABASE import_db")
                
                # Re-connect to get fresh stats avoiding any lingering state
                with self._get_connection() as conn:
                    articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
                    images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                
                print(f"[Storage] DB merged: {articles} articles, {images} images")
                return {'articles': articles, 'images': images}
        
        # JSON file (legacy)
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        imported = {'articles': 0, 'images': 0}
        
        with self._get_connection() as conn:
            if not merge:
                # Clear existing data
                conn.execute("DELETE FROM articles")
                conn.execute("DELETE FROM images")
                conn.execute("DELETE FROM seen_urls")
            
            # Import articles
            for article in data.get('articles', []):
                try:
                    cols = ', '.join(article.keys())
                    vals = ', '.join(['?' for _ in article])
                    conn.execute(
                        f"INSERT OR {'REPLACE' if merge else 'IGNORE'} INTO articles ({cols}) VALUES ({vals})",
                        list(article.values())
                    )
                    
                    # Also add to seen_urls
                    conn.execute(
                        "INSERT OR IGNORE INTO seen_urls VALUES (?, ?, ?, ?)",
                        (article['url'], article['id'], article.get('source_name', ''), article.get('crawled_at', ''))
                    )
                    imported['articles'] += 1
                except Exception as e:
                    print(f"[Import] Article error: {e}")
            
            # Import images
            for image in data.get('images', []):
                try:
                    cols = ', '.join(image.keys())
                    vals = ', '.join(['?' for _ in image])
                    conn.execute(
                        f"INSERT OR {'REPLACE' if merge else 'IGNORE'} INTO images ({cols}) VALUES ({vals})",
                        list(image.values())
                    )
                    imported['images'] += 1
                except Exception as e:
                    print(f"[Import] Image error: {e}")
            
            conn.commit()
        
        print(f"[Storage] Imported: {imported['articles']} articles, {imported['images']} images")
        return imported
    
    def clear_db(self):
        """Clear all data (use with caution!)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM articles")
            conn.execute("DELETE FROM images")
            conn.execute("DELETE FROM seen_urls")
            conn.execute("DELETE FROM scan_state")
            conn.execute("DELETE FROM error_log")
            conn.commit()
        print("[Storage] Database cleared!")
    
    def auto_prune(self, days: int = 7):
        """
        Automatically delete old discarded articles.
        Also deletes associated images from disk.
        """
        from pathlib import Path
        import shutil
        
        cutoff = (datetime.utcnow() - __import__('datetime').timedelta(days=days)).isoformat()
        
        with self._get_connection() as conn:
            # Get articles to delete
            cursor = conn.execute(
                "SELECT id FROM articles WHERE status = -1 AND crawled_at < ?",
                (cutoff,)
            )
            article_ids = [row['id'] for row in cursor.fetchall()]
            
            if not article_ids:
                return 0
            
            # Delete images from disk
            for article_id in article_ids:
                img_dir = Path("data/images") / article_id
                if img_dir.exists():
                    shutil.rmtree(img_dir, ignore_errors=True)
            
            # Delete from DB
            placeholders = ','.join('?' * len(article_ids))
            conn.execute(f"DELETE FROM images WHERE article_id IN ({placeholders})", article_ids)
            conn.execute(f"DELETE FROM articles WHERE id IN ({placeholders})", article_ids)
            conn.execute(f"DELETE FROM seen_urls WHERE article_id IN ({placeholders})", article_ids)
            conn.commit()
        
        print(f"[Storage] Pruned {len(article_ids)} old discarded articles")
        return len(article_ids)


# === Singleton ===
_storage: Optional[Storage] = None

def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage


# === CLI Test ===
if __name__ == "__main__":
    storage = get_storage()
    stats = storage.get_stats()
    print("\n=== Storage Stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
