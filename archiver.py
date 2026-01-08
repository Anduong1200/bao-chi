"""
Auto-Archiver Module - Flash News Hunter
Immediately captures article content when URL is detected.
"Capture First, Review Later" - No waiting for user interaction.
"""

import asyncio
import aiohttp
from datetime import datetime
from typing import Optional, Callable, List
from dataclasses import dataclass

from config import get_config
from storage import get_storage, Article, STATUS_NEW
from parser import ArticleParser
from scanner import ArticleLink


class AutoArchiver:
    """
    Captures article content immediately upon detection.
    
    Flow:
    1. Receive URL from Scanner
    2. Check if already seen (dedup)
    3. IMMEDIATELY fetch HTML content
    4. Parse and save to DB with status=NEW
    5. Notify UI via callback
    """
    
    def __init__(self, on_captured: Optional[Callable[[Article], None]] = None):
        """
        Args:
            on_captured: Callback when article is captured (for UI push)
        """
        self.config = get_config()
        self.storage = get_storage()
        self.parser = ArticleParser()
        self.on_captured = on_captured
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {
            'captured': 0,
            'skipped': 0,
            'failed': 0
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            
            # Get proxy if configured
            proxy = self.config.get_proxy()
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=self.config.headers
            )
            
            # Store proxy for use in requests
            self._proxy = proxy
        return self._session
    
    def _get_proxy_url(self) -> Optional[str]:
        """Get current proxy URL for request."""
        return getattr(self, '_proxy', None) or self.config.get_proxy()
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def capture(self, link: ArticleLink, source_name: str) -> Optional[Article]:
        """
        Capture a single article immediately.
        
        Args:
            link: ArticleLink from scanner
            source_name: Name of source config
            
        Returns:
            Article if captured, None if skipped/failed
        """
        # 1. Check if already seen
        if self.storage.is_seen(link.url):
            self._stats['skipped'] += 1
            return None
        
        # 2. IMMEDIATELY fetch content
        session = await self._get_session()
        proxy = self._get_proxy_url()  # May be None if not configured
        
        try:
            async with session.get(link.url, proxy=proxy) as resp:
                # Handle rate limiting (CRITICAL for anti-ban)
                if resp.status == 429 or resp.status == 403:
                    print(f"[Archiver] ⚠️ RATE LIMITED ({resp.status}): {link.url[:40]}")
                    self._stats['failed'] += 1
                    # Add source to cooldown (caller should handle)
                    return None
                
                if resp.status != 200:
                    print(f"[Archiver] HTTP {resp.status}: {link.url[:50]}")
                    self._stats['failed'] += 1
                    return None
                
                html = await resp.text()
        
        except asyncio.TimeoutError:
            print(f"[Archiver] Timeout: {link.url[:50]}")
            self._stats['failed'] += 1
            return None
        except Exception as e:
            print(f"[Archiver] Fetch error: {e}")
            self._stats['failed'] += 1
            return None
        
        # 3. Parse content
        try:
            parsed = self.parser.parse(html, link.url, source_name)
        except Exception as e:
            print(f"[Archiver] Parse error: {e}")
            self._stats['failed'] += 1
            return None
        
        # 4. Build Article object
        article = Article(
            id=link.article_id or str(abs(hash(link.url))),
            source=link.url.split('/')[2],  # Domain
            source_name=source_name,
            url=link.url,
            title=parsed.get('title', link.title or 'Untitled'),
            sapo=parsed.get('sapo', ''),
            author=parsed.get('author', ''),
            content_text=parsed.get('content_text', ''),
            content_html=parsed.get('content_html', ''),
            images=parsed.get('images', []),
            published_at=parsed.get('published_at', link.published or ''),
            crawled_at=datetime.utcnow().isoformat(),
            status=STATUS_NEW,
            link_alive=True,
            category=parsed.get('category', '')
        )
        
        # 5. Save to DB
        if self.storage.save_article(article):
            self._stats['captured'] += 1
            print(f"[Archiver] ✓ {article.title[:40]}...")
            
            # 5b. Download images physically (async background)
            asyncio.create_task(self._download_images(article))
            
            # 6. Notify UI
            if self.on_captured:
                self.on_captured(article)
            
            return article
        
        return None
    
    async def _download_images(self, article: Article):
        """Download article images to local storage."""
        if not article.images:
            return
        
        from pathlib import Path
        
        # Create article image folder
        img_dir = Path("data/images") / article.id
        img_dir.mkdir(parents=True, exist_ok=True)
        
        session = await self._get_session()
        
        for i, img_url in enumerate(article.images[:10]):  # Max 10 images
            try:
                async with session.get(img_url) as resp:
                    if resp.status != 200:
                        continue
                    
                    # Determine extension from content-type
                    content_type = resp.headers.get('content-type', '')
                    ext = 'jpg'
                    if 'png' in content_type:
                        ext = 'png'
                    elif 'gif' in content_type:
                        ext = 'gif'
                    elif 'webp' in content_type:
                        ext = 'webp'
                    
                    # Save image
                    img_path = img_dir / f"{i}.{ext}"
                    content = await resp.read()
                    
                    with open(img_path, 'wb') as f:
                        f.write(content)
                    
                    # Update DB with local path
                    image_id = self.storage.save_image(article.id, img_url, str(img_path))
                    
            except Exception as e:
                print(f"[Archiver] Image download failed: {e}")
    
    async def capture_batch(self, links: List[ArticleLink], source_name: str) -> List[Article]:
        """
        Capture multiple articles concurrently.
        
        Args:
            links: List of ArticleLinks from scanner
            source_name: Source config name
            
        Returns:
            List of captured Articles
        """
        # Filter already-seen URLs first
        new_urls = self.storage.filter_new_urls([l.url for l in links])
        new_links = [l for l in links if l.url in new_urls]
        
        if not new_links:
            return []
        
        print(f"[Archiver] Capturing {len(new_links)} new articles from {source_name}...")
        
        # Capture concurrently (with limit)
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent fetches
        
        async def capture_with_limit(link):
            async with semaphore:
                return await self.capture(link, source_name)
        
        tasks = [capture_with_limit(link) for link in new_links]
        results = await asyncio.gather(*tasks)
        
        return [a for a in results if a is not None]
    
    async def check_link_alive(self, url: str) -> bool:
        """Check if original link is still alive."""
        session = await self._get_session()
        
        try:
            async with session.head(url, allow_redirects=True) as resp:
                alive = resp.status == 200
                self.storage.update_link_status(url, alive)
                return alive
        except:
            self.storage.update_link_status(url, False)
            return False
    
    async def check_all_links(self, limit: int = 50):
        """Check link status for recent articles."""
        articles = self.storage.get_stream(limit)
        
        for article in articles:
            alive = await self.check_link_alive(article.url)
            if not alive:
                print(f"[Archiver] 🔴 Link dead: {article.title[:30]}...")
    
    def get_stats(self) -> dict:
        return self._stats.copy()


# === CLI Test ===
if __name__ == "__main__":
    from scanner import Scanner
    
    async def test():
        config = get_config()
        sources = config.get_enabled_sources()
        
        if not sources:
            print("No sources!")
            return
        
        source = sources[0]
        print(f"Testing with: {source.name}")
        
        # Scan
        scanner = Scanner(source)
        links = await scanner.scan()
        await scanner.close()
        
        print(f"Found {len(links)} links")
        
        # Archive
        def on_captured(article):
            print(f"  → Captured: {article.title[:50]}")
        
        archiver = AutoArchiver(on_captured=on_captured)
        articles = await archiver.capture_batch(links[:5], source.name)
        await archiver.close()
        
        print(f"\nCaptured {len(articles)} articles")
        print(f"Stats: {archiver.get_stats()}")
    
    asyncio.run(test())
