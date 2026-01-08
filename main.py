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
        
        # Auto cleanup on start if enabled
        if self.config.cleanup.run_on_start:
            pruned = self.storage.auto_prune(self.config.cleanup.discard_after_days)
            if pruned:
                self._log(f"Pruned {pruned} old articles", "info")
        
        # Main loop
        try:
            while self._running:
                # Check for config hot reload
                from config import check_reload
                if check_reload():
                    self._log("Config changed! Reloading...", "warning")
                    await self._reload_scanners()
                
                await self._capture_cycle()
                
                # Wait for next cycle with RANDOM JITTER (anti-bot detection)
                import random
                jitter = random.uniform(0.5, 2.0)  # Random 0.5-2s extra
                sleep_time = self.poll_interval + jitter
                
                for _ in range(int(sleep_time)):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
        
        finally:
            await self._cleanup()
    
    async def _reload_scanners(self):
        """Reload scanners when config changes."""
        # Close old scanners
        for scanner in self._scanners.values():
            await scanner.close()
        self._scanners.clear()
        
        # Reload config
        from config import get_config
        self.config = get_config()
        
        # Create new scanners
        for source in self.config.get_enabled_sources():
            self._scanners[source.name] = Scanner(source)
        
        self._log(f"Reloaded {len(self._scanners)} scanners", "success")
    
    async def _capture_cycle(self):
        """Single capture cycle: scan ALL sources CONCURRENTLY."""
        self._stats['scans'] += 1
        self._stats['last_scan'] = datetime.now().isoformat()
        
        # Create tasks for all sources (CONCURRENT)
        tasks = [
            self._scan_source(name, scanner) 
            for name, scanner in self._scanners.items()
        ]
        
        # Run ALL sources in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count total captured
        total_captured = sum(r for r in results if isinstance(r, int))
        
        if total_captured > 0:
            self._stats['captured'] += total_captured
            self._log(f"Cycle complete: +{total_captured} articles", "success")
    
    async def _scan_source(self, source_name: str, scanner) -> int:
        """Scan a single source and capture articles. Returns count."""
        if not self._running:
            return 0
        
        captured = 0
        
        try:
            # 1. Scan for new links
            links = await scanner.scan()
            
            if not links:
                return 0
            
            # 2. Filter already-seen
            new_urls = self.storage.filter_new_urls([l.url for l in links])
            new_links = [l for l in links if l.url in new_urls]
            
            if not new_links:
                return 0
            
            self._log(f"[{source_name}] {len(new_links)} new", "info")
            
            # 3. Capture articles CONCURRENTLY (max 5 at a time)
            semaphore = asyncio.Semaphore(5)
            
            async def capture_with_limit(link):
                async with semaphore:
                    return await self._archiver.capture(link, source_name)
            
            capture_tasks = [capture_with_limit(link) for link in new_links]
            articles = await asyncio.gather(*capture_tasks, return_exceptions=True)
            
            captured = sum(1 for a in articles if a is not None and not isinstance(a, Exception))
        
        except Exception as e:
            self._log(f"[{source_name}] Error: {e}", "error")
        
        return captured
    
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
