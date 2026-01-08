"""
Scanner Module - News Crawler
Lightweight "Rada" for high-frequency article discovery.
Supports RSS, Sitemap XML, and HTML fallback.
"""

import asyncio
import aiohttp
import re
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass
import xml.etree.ElementTree as ET

from config import get_config, SourceConfig


@dataclass
class ArticleLink:
    """Discovered article metadata from scanner."""
    url: str
    title: str
    article_id: str
    published: Optional[str] = None
    
    def __hash__(self):
        return hash(self.url)
    
    def __eq__(self, other):
        return self.url == other.url


class Scanner:
    """
    Lightweight article scanner for a specific source.
    - RSS mode: Parse XML feed (50x lighter than HTML)
    - Sitemap mode: Parse sitemap XML
    - HTML mode: Fallback to scraping homepage
    """
    
    # Multiple patterns for different sites
    ARTICLE_ID_PATTERNS = [
        re.compile(r'-(\d{15,20})\.htm'),     # Thanh Niên: -185260107154311932.htm
        re.compile(r'-(\d{6,10})\.htm'),       # Tuổi Trẻ: -20260108.htm
        re.compile(r'/(\d{6,12})\.html?'),     # VnExpress: /4851234.html
        re.compile(r'-(\d+)\.html?'),          # Generic: -123456.html
        re.compile(r'/([a-z0-9-]+)-\d+\.htm'), # Slug-based
    ]
    
    def __init__(self, source: SourceConfig):
        """
        Initialize scanner for a specific source.
        
        Args:
            source: SourceConfig with URL, type, and frequency
        """
        self.source = source
        self.config = get_config()
        self._last_modified = None
        self._etag = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.worker.timeout + 5,  # Extra time for XML parsing
                connect=5
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=self.config.headers
            )
        return self._session
    
    async def close(self):
        """Close session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _extract_article_id(self, url: str) -> str:
        """Extract article ID from URL using multiple patterns."""
        for pattern in self.ARTICLE_ID_PATTERNS:
            match = pattern.search(url)
            if match:
                return match.group(1)
        # Fallback: use last path segment or hash
        match = re.search(r'/([^/]+?)(?:\.htm|\.html)?$', url)
        if match:
            return match.group(1)[:50]
        return str(abs(hash(url)))
    
    async def scan(self) -> List[ArticleLink]:
        """
        Scan for new articles.
        
        Returns:
            List of ArticleLink found
        """
        if self.source.type == "rss":
            return await self._scan_xml()
        else:
            return await self._scan_html()
    
    async def _scan_xml(self) -> List[ArticleLink]:
        """
        Scan RSS/Sitemap XML feed.
        Handles both RSS <item> and Sitemap <url> formats.
        """
        url = self.source.url
        session = await self._get_session()
        
        # Build conditional headers
        headers = dict(self.config.headers)
        if self._last_modified:
            headers['If-Modified-Since'] = self._last_modified
        if self._etag:
            headers['If-None-Match'] = self._etag
        
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 304:
                    print(f"[Scanner:{self.source.name}] XML not modified (304)")
                    return []
                
                if resp.status != 200:
                    print(f"[Scanner:{self.source.name}] XML error: {resp.status}")
                    return []
                
                self._last_modified = resp.headers.get('Last-Modified')
                self._etag = resp.headers.get('ETag')
                
                content = await resp.text()
                return self._parse_xml(content)
                
        except asyncio.TimeoutError:
            print(f"[Scanner:{self.source.name}] XML timeout")
            return []
        except Exception as e:
            print(f"[Scanner:{self.source.name}] XML error: {e}")
            return []
    
    def _parse_xml(self, xml_content: str) -> List[ArticleLink]:
        """
        Parse RSS or Sitemap XML.
        Automatically detects format and extracts articles.
        """
        articles = []
        
        try:
            # Clean XML: remove namespace declarations and prefixes
            xml_content = re.sub(r'\sxmlns[^=]*="[^"]*"', '', xml_content)
            xml_content = re.sub(r'<(/?)(\w+):', r'<\1', xml_content)
            
            root = ET.fromstring(xml_content)
            
            # Try RSS format first (has <item> elements)
            items = root.findall('.//item')
            if items:
                articles = self._parse_rss_items(items)
            else:
                # Try Sitemap format (<url> elements)
                urls = root.findall('.//url')
                if urls:
                    articles = self._parse_sitemap_urls(urls)
            
            print(f"[Scanner:{self.source.name}] XML: Found {len(articles)} articles")
            
        except ET.ParseError as e:
            print(f"[Scanner:{self.source.name}] XML parse error: {e}")
        
        return articles
    
    def _parse_rss_items(self, items) -> List[ArticleLink]:
        """Parse RSS <item> elements."""
        articles = []
        
        for item in items:
            link_elem = item.find('link')
            title_elem = item.find('title')
            pub_date_elem = item.find('pubDate')
            
            if link_elem is None:
                continue
            
            # Handle link as text or CDATA
            url = link_elem.text.strip() if link_elem.text else ""
            if not url:
                continue
            
            title = ""
            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()
            
            pub_date = None
            if pub_date_elem is not None and pub_date_elem.text:
                pub_date = pub_date_elem.text.strip()
            
            article_id = self._extract_article_id(url)
            
            articles.append(ArticleLink(
                url=url,
                title=title,
                article_id=article_id,
                published=pub_date
            ))
        
        return articles
    
    def _parse_sitemap_urls(self, urls) -> List[ArticleLink]:
        """Parse Sitemap <url> elements."""
        articles = []
        
        for url_elem in urls:
            loc_elem = url_elem.find('loc')
            lastmod_elem = url_elem.find('lastmod')
            
            # Also check for news:news elements (Google News sitemap)
            news_elem = url_elem.find('.//news')
            title_elem = url_elem.find('.//title') if news_elem is not None else None
            
            if loc_elem is None or not loc_elem.text:
                continue
            
            url = loc_elem.text.strip()
            
            # Skip non-article URLs (categories, tags, etc.)
            if not self._is_article_url(url):
                continue
            
            title = ""
            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()
            
            pub_date = None
            if lastmod_elem is not None and lastmod_elem.text:
                pub_date = lastmod_elem.text.strip()
            
            article_id = self._extract_article_id(url)
            
            articles.append(ArticleLink(
                url=url,
                title=title,
                article_id=article_id,
                published=pub_date
            ))
        
        return articles
    
    def _is_article_url(self, url: str) -> bool:
        """Check if URL is likely an article (not category/tag page)."""
        # Skip common non-article patterns
        skip_patterns = [
            r'/tag/', r'/category/', r'/author/', r'/page/',
            r'/search/', r'/login/', r'/register/',
            r'\.(css|js|png|jpg|gif|ico|svg)$',
        ]
        for pattern in skip_patterns:
            if re.search(pattern, url, re.I):
                return False
        
        # Must end with article-like extension
        return bool(re.search(r'\.(htm|html|aspx)$', url, re.I)) or \
               bool(re.search(r'/\d+/?$', url))  # Ends with number
    
    async def _scan_html(self) -> List[ArticleLink]:
        """
        Scan HTML homepage (fallback).
        """
        from bs4 import BeautifulSoup
        
        url = self.source.url
        session = await self._get_session()
        
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"[Scanner:{self.source.name}] HTML error: {resp.status}")
                    return []
                
                html = await resp.text()
                
        except Exception as e:
            print(f"[Scanner:{self.source.name}] HTML error: {e}")
            return []
        
        soup = BeautifulSoup(html, 'lxml')
        
        # Generic article link selectors
        selectors = [
            'a.box-category-link-title',  # Thanh Niên
            'h3 a', 'h2 a',               # Common patterns
            '.article-title a',
            '.news-title a',
        ]
        
        articles = []
        seen_urls = set()
        
        for selector in selectors:
            for link in soup.select(selector):
                href = link.get('href', '')
                if not href:
                    continue
                
                # Make absolute URL
                if href.startswith('/'):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                
                if not self._is_article_url(href):
                    continue
                
                title = link.get_text(strip=True)
                article_id = self._extract_article_id(href)
                
                articles.append(ArticleLink(
                    url=href,
                    title=title,
                    article_id=article_id
                ))
        
        print(f"[Scanner:{self.source.name}] HTML: Found {len(articles)} articles")
        return articles
    
    async def check_exists(self, url: str) -> bool:
        """Check if article still exists (for delete detection)."""
        session = await self._get_session()
        
        try:
            async with session.head(url, allow_redirects=True) as resp:
                return resp.status == 200
        except:
            return False


def create_scanner(source: SourceConfig) -> Scanner:
    """Factory function to create a scanner for a source."""
    return Scanner(source)


# === CLI Test ===
if __name__ == "__main__":
    async def test():
        config = get_config()
        
        sources = config.get_enabled_sources()
        if not sources:
            print("No sources configured!")
            return
        
        print(f"=== Testing {len(sources)} Sources ===\n")
        
        for source in sources:
            print(f"--- {source.name} ---")
            print(f"URL: {source.url}")
            print(f"Type: {source.type}")
            
            scanner = Scanner(source)
            articles = await scanner.scan()
            
            print(f"Found: {len(articles)} articles")
            if articles:
                print(f"  Sample: {articles[0].title[:50]}..." if articles[0].title else f"  URL: {articles[0].url}")
            print()
            
            await scanner.close()
    
    asyncio.run(test())
