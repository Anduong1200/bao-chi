"""
Main Orchestrator - Flash News Hunter
High-frequency capture loop: Scan → Archive → Push to UI

Core principle: "Capture First, Review Later"
"""

import asyncio
import signal
from datetime import datetime
from typing import Optional, Callable, List, Dict

from config import get_config, SourceConfig
from storage import get_storage, Article
from scanner import Scanner, ArticleLink
from archiver import AutoArchiver


class FlashNewsHunter:
    """
    Main orchestrator for Flash News Hunter.
    
    Features:
    - High-frequency polling (3-5 seconds)
    - Auto-capture on URL detection
    - Real-time push to UI via callbacks
    - Link health monitoring
    """
    
    def __init__(
        self,
        poll_interval: int = 5,
        on_article: Optional[Callable[[Article], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ):
        """
        Args:
            poll_interval: Seconds between scans (default: 5)
            on_article: Callback when article captured
            on_log: Callback for log messages (msg, level)
        """
        self.config = get_config()
        self.storage = get_storage()
        self.poll_interval = poll_interval
        self.on_article = on_article
        self.on_log = on_log
        
        self._running = False
        self._scanners: Dict[str, Scanner] = {}
        self._archiver: Optional[AutoArchiver] = None
        self._stats = {
            'scans': 0,
            'captured': 0,
            'last_scan': None
        }
    
    def _log(self, msg: str, level: str = "info"):
        """Send log message to callback."""
        print(f"[Hunter] {msg}")
        if self.on_log:
            self.on_log(msg, level)
    
    async def start(self):
        """Start the capture loop."""
        self._running = True
        
        sources = self.config.get_enabled_sources()
        if not sources:
            self._log("No enabled sources!", "error")
            return
        
        self._log(f"Starting with {len(sources)} sources, {self.poll_interval}s interval", "success")
        
        # Initialize scanners
        for source in sources:
            self._scanners[source.name] = Scanner(source)
        
        # Initialize archiver with callback
        self._archiver = AutoArchiver(on_captured=self.on_article)
        
        # Main loop
        try:
            while self._running:
                await self._capture_cycle()
                
                # Wait for next cycle
                for _ in range(self.poll_interval):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
        
        finally:
            await self._cleanup()
    
    async def _capture_cycle(self):
        """Single capture cycle: scan all sources and archive."""
        self._stats['scans'] += 1
        self._stats['last_scan'] = datetime.now().isoformat()
        
        total_captured = 0
        
        for source_name, scanner in self._scanners.items():
            if not self._running:
                break
            
            try:
                # 1. Scan for new links
                links = await scanner.scan()
                
                if not links:
                    continue
                
                # 2. Filter already-seen
                new_urls = self.storage.filter_new_urls([l.url for l in links])
                new_links = [l for l in links if l.url in new_urls]
                
                if not new_links:
                    continue
                
                self._log(f"[{source_name}] {len(new_links)} new articles", "info")
                
                # 3. IMMEDIATELY capture each
                for link in new_links:
                    if not self._running:
                        break
                    
                    article = await self._archiver.capture(link, source_name)
                    if article:
                        total_captured += 1
                
            except Exception as e:
                self._log(f"[{source_name}] Error: {e}", "error")
        
        if total_captured > 0:
            self._stats['captured'] += total_captured
            self._log(f"Cycle complete: +{total_captured} articles", "success")
    
    async def stop(self):
        """Stop the capture loop gracefully."""
        self._log("Stopping...", "warning")
        self._running = False
    
    async def _cleanup(self):
        """Clean up resources."""
        for scanner in self._scanners.values():
            await scanner.close()
        
        if self._archiver:
            await self._archiver.close()
        
        self._log("Stopped", "warning")
    
    def is_running(self) -> bool:
        return self._running
    
    def get_stats(self) -> dict:
        """Get current stats."""
        storage_stats = self.storage.get_stats()
        return {
            **self._stats,
            'storage': storage_stats
        }
    
    async def check_dead_links(self, limit: int = 50):
        """Check and update link status for recent articles."""
        self._log("Checking link health...", "info")
        
        articles = self.storage.get_stream(limit)
        dead_count = 0
        
        for article in articles:
            alive = await self._archiver.check_link_alive(article.url)
            if not alive:
                dead_count += 1
                self._log(f"🔴 Dead: {article.title[:30]}...", "warning")
        
        self._log(f"Link check: {dead_count}/{len(articles)} dead", "info")


class SourceMonitor:
    """Monitor a single source with high frequency."""
    
    def __init__(
        self,
        source: SourceConfig,
        archiver: AutoArchiver,
        poll_interval: int = 5,
        on_log: Optional[Callable] = None
    ):
        self.source = source
        self.archiver = archiver
        self.poll_interval = poll_interval
        self.on_log = on_log
        
        self.scanner = Scanner(source)
        self._running = False
    
    def _log(self, msg: str, level: str = "info"):
        if self.on_log:
            self.on_log(msg, level)
    
    async def run(self):
        """Run continuous monitoring."""
        self._running = True
        self._log(f"[{self.source.name}] Started ({self.poll_interval}s)", "success")
        
        while self._running:
            try:
                # Scan
                links = await self.scanner.scan()
                
                # Capture new ones
                if links:
                    articles = await self.archiver.capture_batch(links, self.source.name)
                    if articles:
                        self._log(f"[{self.source.name}] +{len(articles)}", "info")
            
            except Exception as e:
                self._log(f"[{self.source.name}] Error: {e}", "error")
            
            # Wait
            for _ in range(self.poll_interval):
                if not self._running:
                    break
                await asyncio.sleep(1)
        
        await self.scanner.close()
        self._log(f"[{self.source.name}] Stopped", "warning")
    
    def stop(self):
        self._running = False


# === CLI Entry Point ===
async def main():
    """CLI entry point."""
    print("=" * 50)
    print("  FLASH NEWS HUNTER")
    print("  Capture First, Review Later")
    print("=" * 50)
    
    def on_article(article: Article):
        print(f"  📰 {article.source_name}: {article.title[:50]}...")
    
    def on_log(msg: str, level: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {"success": "✓", "error": "✗", "warning": "⚠", "info": "•"}.get(level, "•")
        print(f"[{timestamp}] {icon} {msg}")
    
    hunter = FlashNewsHunter(
        poll_interval=5,
        on_article=on_article,
        on_log=on_log
    )
    
    # Handle Ctrl+C
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(hunter.stop()))
        except NotImplementedError:
            pass  # Windows
    
    try:
        await hunter.start()
    except KeyboardInterrupt:
        await hunter.stop()
    
    # Final stats
    stats = hunter.get_stats()
    print("\n=== Session Stats ===")
    print(f"  Scans: {stats['scans']}")
    print(f"  Captured: {stats['captured']}")
    print(f"  DB Total: {stats['storage']['total']}")


if __name__ == "__main__":
    asyncio.run(main())
