"""
Worker Module - News Crawler
Worker pool for concurrent content downloading.
Implements fail-fast, priority queue, and retry logic.
"""

import asyncio
import aiohttp
from datetime import datetime
from typing import Optional, Callable, Any
from dataclasses import dataclass

from config import get_config
from parser import get_parser
from storage import get_storage, Article
from scanner import ArticleLink


@dataclass
class WorkItem:
    """Item in the work queue."""
    link: ArticleLink
    source_name: str
    site_code: str
    priority: int = 0  # Lower = higher priority
    retries: int = 0
    
    def __lt__(self, other):
        return self.priority < other.priority


class WorkerPool:
    """
    Concurrent worker pool for article downloading.
    Features:
    - Priority queue (newest first)
    - Fail-fast (5s timeout)
    - Auto-retry with backoff
    - Graceful shutdown
    """
    
    def __init__(self, queue: asyncio.PriorityQueue, on_article: Optional[Callable] = None):
        """
        Args:
            queue: Shared priority queue with WorkItems
            on_article: Callback when article is saved (for GUI updates)
        """
        self.queue = queue
        self.config = get_config()
        self.storage = get_storage()
        self.parser = get_parser()
        self.on_article = on_article
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._stats = {
            'processed': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.worker.timeout,
                connect=3
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=self.config.headers
            )
        return self._session
    
    async def close(self):
        """Close session."""
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def run_worker(self, worker_id: int):
        """
        Single worker coroutine.
        Continuously pulls from queue and processes.
        """
        print(f"[Worker-{worker_id}] Started")
        self._running = True
        
        while self._running:
            try:
                # Get item with timeout to allow graceful shutdown
                try:
                    _, item = await asyncio.wait_for(
                        self.queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if item is None:  # Poison pill for shutdown
                    break
                
                await self._process_item(item, worker_id)
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Worker-{worker_id}] Error: {e}")
        
        print(f"[Worker-{worker_id}] Stopped")
    
    async def _process_item(self, item: WorkItem, worker_id: int):
        """Process single work item."""
        url = item.link.url
        self._stats['processed'] += 1
        
        # Check if already in DB (deduplication)
        if self.storage.is_seen(url):
            self._stats['skipped'] += 1
            return
        
        try:
            # Fetch article HTML
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status == 404:
                    print(f"[Worker-{worker_id}] 404: {url}")
                    self._stats['failed'] += 1
                    return
                
                if resp.status != 200:
                    # Retry logic
                    if item.retries < self.config.worker.max_retries:
                        item.retries += 1
                        item.priority += 10  # Lower priority for retries
                        await self.queue.put((item.priority, item))
                        print(f"[Worker-{worker_id}] Retry {item.retries}: {url}")
                    else:
                        self._stats['failed'] += 1
                    return
                
                html = await resp.text()
            
            # Parse article
            article = self.parser.parse(html, url)
            if article is None:
                self._stats['failed'] += 1
                return
            
            # Download images if enabled
            if self.config.storage.save_images and article.images:
                local_images = []
                for i, img_url in enumerate(article.images[:10]):  # Limit to 10 images
                    local_path = await self.storage.download_image(
                        img_url, article.id, i
                    )
                    if local_path:
                        local_images.append(local_path)
                article.images = local_images
            
            # Save to database
            if self.storage.save_article(article):
                self._stats['success'] += 1
                print(f"[Worker-{worker_id}] ✓ {article.title[:50]}...")
                
                # Callback for GUI updates
                if self.on_article:
                    try:
                        self.on_article(article)
                    except:
                        pass
            else:
                self._stats['skipped'] += 1
                
        except asyncio.TimeoutError:
            print(f"[Worker-{worker_id}] Timeout: {url}")
            self._stats['failed'] += 1
        except Exception as e:
            print(f"[Worker-{worker_id}] Error processing {url}: {e}")
            self._stats['failed'] += 1
    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return dict(self._stats)


async def create_worker_pool(
    queue: asyncio.PriorityQueue,
    num_workers: int = 5,
    on_article: Optional[Callable] = None
) -> tuple:
    """
    Create and start worker pool.
    
    Args:
        queue: Shared priority queue
        num_workers: Number of concurrent workers
        on_article: Callback when article is saved
        
    Returns:
        Tuple of (pool, tasks)
    """
    pool = WorkerPool(queue, on_article)
    tasks = [
        asyncio.create_task(pool.run_worker(i))
        for i in range(num_workers)
    ]
    return pool, tasks


# === CLI Test ===
if __name__ == "__main__":
    async def test():
        from scanner import get_scanner, ArticleLink
        
        queue = asyncio.PriorityQueue()
        pool, tasks = await create_worker_pool(queue, num_workers=3)
        
        # Get some articles from scanner
        scanner = get_scanner()
        articles = await scanner.scan()
        
        print(f"\n=== Testing Worker Pool with {len(articles[:5])} articles ===")
        
        # Add to queue
        for i, link in enumerate(articles[:5]):
            item = WorkItem(
                link=link,
                source_name="test",
                site_code="TNO",
                priority=i
            )
            await queue.put((item.priority, item))
        
        # Wait for processing
        await queue.join()
        
        # Shutdown
        await pool.close()
        for task in tasks:
            task.cancel()
        
        # Stats
        print(f"\n=== Stats ===")
        for k, v in pool.get_stats().items():
            print(f"  {k}: {v}")
        
        await scanner.close()
    
    asyncio.run(test())
