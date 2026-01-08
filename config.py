"""
Config Loader - News Crawler
Multi-source configuration with hot-reload support.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
import os


@dataclass
class SourceConfig:
    """Configuration for a single news source."""
    name: str
    url: str
    type: str = "rss"  # rss | html
    site_code: str = "TNO"  # Parser selector key
    frequency: int = 5  # Scan frequency in seconds
    enabled: bool = True


@dataclass
class SystemConfig:
    num_workers: int = 10
    database: str = "news.db"
    log_level: str = "INFO"


@dataclass
class WorkerConfig:
    timeout: int = 5
    max_retries: int = 3
    priority_newest: bool = True


@dataclass  
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class AlertingConfig:
    error_threshold: int = 5
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class StorageConfig:
    type: str = "sqlite"
    path: str = "./data"
    db_name: str = "articles.db"
    save_images: bool = True
    
    @property
    def db_path(self) -> Path:
        return Path(self.path) / self.db_name
    
    @property
    def images_path(self) -> Path:
        return Path(self.path) / "images"


@dataclass
class SelectorSet:
    """Selectors for a specific site."""
    title: str = "h1"
    sapo: str = ".description"
    content: str = "article"
    author: str = ".author"
    time: str = ".time"


@dataclass
class Config:
    system: SystemConfig = field(default_factory=SystemConfig)
    sources: List[SourceConfig] = field(default_factory=list)
    selectors: Dict[str, SelectorSet] = field(default_factory=dict)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    headers: Dict[str, str] = field(default_factory=dict)
    
    _config_path: Optional[Path] = field(default=None, repr=False)
    _last_modified: float = field(default=0, repr=False)
    
    def get_enabled_sources(self) -> List[SourceConfig]:
        """Get only enabled sources."""
        return [s for s in self.sources if s.enabled]
    
    def get_selectors(self, site_code: str) -> SelectorSet:
        """Get selectors for a site code."""
        return self.selectors.get(site_code, SelectorSet())


# Global config instance
_config: Optional[Config] = None
_config_path: Optional[Path] = None


def _parse_source(data: Dict) -> SourceConfig:
    """Parse source configuration."""
    return SourceConfig(
        name=data.get('name', 'Unknown'),
        url=data.get('url', ''),
        type=data.get('type', 'rss'),
        site_code=data.get('site_code', 'TNO'),
        frequency=data.get('frequency', 5),
        enabled=data.get('enabled', True)
    )


def _parse_selectors(data: Dict) -> Dict[str, SelectorSet]:
    """Parse site-specific selectors."""
    result = {}
    for site_code, selectors in data.items():
        result[site_code] = SelectorSet(
            title=selectors.get('title', 'h1'),
            sapo=selectors.get('sapo', '.description'),
            content=selectors.get('content', 'article'),
            author=selectors.get('author', '.author'),
            time=selectors.get('time', '.time')
        )
    return result


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to config.yaml. If None, uses default location.
        
    Returns:
        Config object with all settings.
    """
    global _config, _config_path
    
    if config_path is None:
        candidates = [
            Path("config.yaml"),
            Path(__file__).parent / "config.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break
        else:
            print("[Config] No config.yaml found, using defaults")
            _config = Config()
            return _config
    
    path = Path(config_path)
    _config_path = path
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    if data is None:
        data = {}
    
    # Parse system config
    system_data = data.get('system', {})
    system = SystemConfig(
        num_workers=system_data.get('num_workers', 10),
        database=system_data.get('database', 'news.db'),
        log_level=system_data.get('log_level', 'INFO')
    )
    
    # Parse sources
    sources = [_parse_source(s) for s in data.get('sources', [])]
    
    # Parse selectors
    selectors = _parse_selectors(data.get('selectors', {}))
    
    # Parse worker config
    worker_data = data.get('worker', {})
    worker = WorkerConfig(
        timeout=worker_data.get('timeout', 5),
        max_retries=worker_data.get('max_retries', 3),
        priority_newest=worker_data.get('priority_newest', True)
    )
    
    # Parse alerting
    alerting_data = data.get('alerting', {})
    telegram_data = alerting_data.get('telegram', {})
    alerting = AlertingConfig(
        error_threshold=alerting_data.get('error_threshold', 5),
        telegram=TelegramConfig(
            enabled=telegram_data.get('enabled', False),
            bot_token=telegram_data.get('bot_token', ''),
            chat_id=telegram_data.get('chat_id', '')
        )
    )
    
    # Parse storage
    storage_data = data.get('storage', {})
    storage = StorageConfig(
        type=storage_data.get('type', 'sqlite'),
        path=storage_data.get('path', './data'),
        db_name=storage_data.get('db_name', 'articles.db'),
        save_images=storage_data.get('save_images', True)
    )
    
    # Build config
    config = Config(
        system=system,
        sources=sources,
        selectors=selectors,
        worker=worker,
        alerting=alerting,
        storage=storage,
        headers=data.get('headers', {})
    )
    
    config._config_path = path
    config._last_modified = path.stat().st_mtime
    
    _config = config
    print(f"[Config] Loaded {len(sources)} sources from {path}")
    
    return config


def get_config() -> Config:
    """Get current config, loading if necessary."""
    global _config
    if _config is None:
        return load_config()
    return _config


def check_reload() -> bool:
    """Check if config file was modified and reload if needed."""
    global _config, _config_path
    
    if _config is None or _config_path is None:
        return False
    
    if not _config_path.exists():
        return False
    
    current_mtime = _config_path.stat().st_mtime
    if current_mtime > _config._last_modified:
        print("[Config] File changed, reloading...")
        load_config(str(_config_path))
        return True
    
    return False


def ensure_directories():
    """Create necessary directories based on config."""
    config = get_config()
    Path(config.storage.path).mkdir(parents=True, exist_ok=True)
    config.storage.images_path.mkdir(parents=True, exist_ok=True)
    print(f"[Config] Directories ensured: {config.storage.path}")


# === CLI Test ===
if __name__ == "__main__":
    config = load_config()
    
    print("\n=== System ===")
    print(f"  Workers: {config.system.num_workers}")
    print(f"  Database: {config.system.database}")
    
    print("\n=== Sources ===")
    for src in config.get_enabled_sources():
        print(f"  [{src.site_code}] {src.name}")
        print(f"       URL: {src.url}")
        print(f"       Frequency: {src.frequency}s")
    
    print("\n=== Selectors ===")
    for code, sel in config.selectors.items():
        print(f"  {code}: title={sel.title}")
    
    ensure_directories()
