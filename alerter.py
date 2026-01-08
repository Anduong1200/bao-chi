"""
Alerter Module - News Crawler
Telegram notification for error alerting.
"""

import asyncio
import aiohttp
from typing import Optional
from datetime import datetime

from config import get_config
from storage import get_storage


class Alerter:
    """
    Telegram alerter for error notifications.
    Sends alert when consecutive errors exceed threshold.
    """
    
    def __init__(self):
        self.config = get_config()
        self.storage = get_storage()
        self._error_counts: dict = {}  # source_name -> consecutive errors
        self._last_alert: dict = {}    # source_name -> last alert time
        self._alert_cooldown = 300     # 5 minutes between alerts
    
    async def send_telegram(self, message: str) -> bool:
        """Send message via Telegram bot."""
        telegram = self.config.alerting.telegram
        
        if not telegram.enabled:
            print(f"[Alerter] Telegram disabled, message: {message}")
            return False
        
        if not telegram.bot_token or not telegram.chat_id:
            print(f"[Alerter] Telegram not configured")
            return False
        
        url = f"https://api.telegram.org/bot{telegram.bot_token}/sendMessage"
        payload = {
            "chat_id": telegram.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        print(f"[Alerter] Telegram sent successfully")
                        return True
                    else:
                        print(f"[Alerter] Telegram error: {resp.status}")
                        return False
        except Exception as e:
            print(f"[Alerter] Telegram failed: {e}")
            return False
    
    async def record_error(self, source_name: str, error_type: str, message: str, url: str = ""):
        """
        Record error and check if alert should be sent.
        
        Args:
            source_name: Name of the source
            error_type: Type of error (timeout, parse, network, etc.)
            message: Error message
            url: URL that caused the error
        """
        # Log to database
        self.storage.log_error(source_name, error_type, message, url)
        
        # Increment consecutive error count
        self._error_counts[source_name] = self._error_counts.get(source_name, 0) + 1
        
        # Check threshold
        if self._error_counts[source_name] >= self.config.alerting.error_threshold:
            await self._maybe_send_alert(source_name, error_type, message)
    
    def record_success(self, source_name: str):
        """Reset error count on success."""
        self._error_counts[source_name] = 0
    
    async def _maybe_send_alert(self, source_name: str, error_type: str, message: str):
        """Send alert if cooldown has passed."""
        now = datetime.utcnow().timestamp()
        last = self._last_alert.get(source_name, 0)
        
        if now - last < self._alert_cooldown:
            return  # Still in cooldown
        
        self._last_alert[source_name] = now
        
        # Build alert message
        alert_msg = f"""
🚨 <b>News Crawler Alert</b>

<b>Source:</b> {source_name}
<b>Error Type:</b> {error_type}
<b>Message:</b> {message}
<b>Consecutive Errors:</b> {self._error_counts[source_name]}
<b>Time:</b> {datetime.utcnow().isoformat()}

⚠️ Please check the configuration or website changes.
"""
        await self.send_telegram(alert_msg.strip())
    
    async def send_startup_notification(self):
        """Send notification when crawler starts."""
        config = self.config
        sources = config.get_enabled_sources()
        
        msg = f"""
✅ <b>News Crawler Started</b>

<b>Sources:</b> {len(sources)} enabled
<b>Workers:</b> {config.system.num_workers}
<b>Time:</b> {datetime.utcnow().isoformat()}

Sources:
""" + "\n".join([f"• {s.name}" for s in sources])
        
        await self.send_telegram(msg.strip())
    
    async def send_shutdown_notification(self, stats: dict):
        """Send notification when crawler stops."""
        msg = f"""
⏹️ <b>News Crawler Stopped</b>

<b>Total Articles:</b> {stats.get('total_articles', 0)}
<b>Today Crawled:</b> {stats.get('today_crawled', 0)}
<b>Time:</b> {datetime.utcnow().isoformat()}
"""
        await self.send_telegram(msg.strip())


# === Singleton ===
_alerter: Optional[Alerter] = None


def get_alerter() -> Alerter:
    """Get alerter singleton."""
    global _alerter
    if _alerter is None:
        _alerter = Alerter()
    return _alerter


# === CLI Test ===
if __name__ == "__main__":
    async def test():
        alerter = get_alerter()
        
        print("=== Testing Alerter ===")
        
        # Test error recording
        for i in range(6):
            await alerter.record_error(
                "TestSource",
                "timeout",
                f"Connection timed out (attempt {i+1})"
            )
            print(f"  Error {i+1} recorded")
        
        # Test success reset
        alerter.record_success("TestSource")
        print("  Success recorded, counter reset")
    
    asyncio.run(test())
