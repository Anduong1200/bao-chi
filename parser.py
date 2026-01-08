"""
Parser Module - News Crawler
Multi-site HTML parser with site-specific selectors.
"""

import re
from datetime import datetime
from typing import Optional, List, Tuple
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin

from config import get_config, SelectorSet
from storage import Article


class ArticleParser:
    """
    Multi-site article parser.
    Uses site_code to select appropriate selectors.
    """
    
    REMOVE_TAGS = ['script', 'style', 'iframe', 'noscript', 'svg', 
                   'button', 'input', 'form', 'nav', 'footer', 'aside']
    
    REMOVE_PATTERNS = [
        re.compile(r'(ads?|advert|banner|promo|sponsor|social|share|related|comment)', re.I)
    ]
    
    def __init__(self):
        self.config = get_config()
        self._default_selectors = SelectorSet()
    
    def parse(self, html: str, url: str, source_name: str = "", 
              site_code: str = "TNO") -> Optional[Article]:
        """
        Parse article HTML with site-specific selectors.
        
        Args:
            html: Raw HTML content
            url: Article URL
            source_name: Source config name
            site_code: Site code for selector lookup
            
        Returns:
            Article object or None if parsing fails
        """
        try:
            # Get site-specific selectors
            selectors = self.config.get_selectors(site_code)
            if not selectors.title:
                selectors = self._default_selectors
            
            # Parse HTML
            try:
                soup = BeautifulSoup(html, 'lxml')
            except:
                soup = BeautifulSoup(html, 'html.parser')
            
            article_id = self._extract_id(url)
            source = self._extract_source(url)
            
            title = self._extract_title(soup, selectors)
            sapo = self._extract_sapo(soup, selectors)
            author = self._extract_author(soup, selectors)
            published_at = self._extract_published(soup, selectors)
            content_html, content_text = self._extract_content(soup, selectors)
            images = self._extract_images(soup, url, selectors)
            category = self._extract_category(soup)
            
            if not title:
                print(f"[Parser] No title found for {url}")
                return None
            
            return Article(
                id=article_id,
                source=source,
                source_name=source_name,
                url=url,
                title=title,
                sapo=sapo,
                author=author,
                content_text=content_text,
                content_html=content_html,
                images=images,
                published_at=published_at,
                crawled_at=datetime.utcnow().isoformat(),
                status="active",
                category=category
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Parser] Error parsing {url}: {e}")
            return None
    
    def _extract_id(self, url: str) -> str:
        """Extract article ID from URL."""
        # 1. Tuoi Tre / Thanh Nien pattern (long ID at end)
        # Matches: ...-20260108203142592.htm
        match = re.search(r'-(\d{14,20})\.htm', url)
        if match:
            return match.group(1)
        
        # 2. VnExpress pattern
        # Matches: ...-4851234.html
        match = re.search(r'-(\d{6,10})\.html', url)
        if match:
            return match.group(1)
        
        # 3. Generic: last path segment
        match = re.search(r'/([^/]+?)(?:\.htm|\.html)?$', url)
        if match:
            return match.group(1)[:50]
        
        return str(abs(hash(url)))

    def _extract_source(self, url: str) -> str:
        """Extract source name from URL."""
        if 'thanhnien.vn' in url:
            return 'thanhnien'
        elif 'vnexpress.net' in url:
            return 'vnexpress'
        elif 'cafef.vn' in url:
            return 'cafef'
        elif 'tuoitre.vn' in url:
            return 'tuoitre'
        else:
            match = re.search(r'://(?:www\.)?([^/]+)', url)
            return match.group(1) if match else 'unknown'

    def _safe_get(self, tag, attr: str, default=None):
        """Helper to get attribute safely, avoiding NoneType crash."""
        if tag:
            return tag.get(attr, default)
        return default

    def _extract_title(self, soup: BeautifulSoup, selectors: SelectorSet) -> str:
        """Extract article title."""
        title_elem = soup.select_one(selectors.title)
        return title_elem.get_text(strip=True) if title_elem else ""

    def _extract_sapo(self, soup: BeautifulSoup, selectors: SelectorSet) -> str:
        """Extract article sapo (summary)."""
        sapo_elem = soup.select_one(selectors.sapo)
        return sapo_elem.get_text(strip=True) if sapo_elem else ""

    def _extract_author(self, soup: BeautifulSoup, selectors: SelectorSet) -> str:
        """Extract article author."""
        author_elem = soup.select_one(selectors.author)
        return author_elem.get_text(strip=True) if author_elem else ""

    def _extract_published(self, soup: BeautifulSoup, selectors: SelectorSet) -> str:
        """Extract published time."""
        elem = soup.select_one(selectors.time)
        if elem:
            time_text = elem.get_text(strip=True)
            parsed = self._parse_vn_date(time_text)
            if parsed:
                return parsed
        
        # Safe meta tag access
        meta = soup.find('meta', property='article:published_time')
        content = self._safe_get(meta, 'content')
        if content:
            return content
        
        # Safe time tag access
        time_elem = soup.find('time')
        dt = self._safe_get(time_elem, 'datetime')
        if dt:
            return dt
        
        return datetime.utcnow().isoformat()
    
    def _parse_vn_date(self, text: str) -> Optional[str]:
        """Parse Vietnamese date format to ISO."""
        patterns = [
            (r'(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})/(\d{4})', 
             lambda m: f"{m.group(5)}-{m.group(4).zfill(2)}-{m.group(3).zfill(2)}T{m.group(1).zfill(2)}:{m.group(2)}:00"),
            (r'(\d{1,2})/(\d{1,2})/(\d{4})\s*-?\s*(\d{1,2}):(\d{2})',
             lambda m: f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}T{m.group(4).zfill(2)}:{m.group(5)}:00"),
            (r'(\d{1,2})/(\d{1,2})/(\d{4})',
             lambda m: f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}T00:00:00"),
        ]
        
        for pattern, formatter in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return formatter(match)
                except:
                    pass
        
        return None
    
    def _extract_content(self, soup: BeautifulSoup, selectors: SelectorSet) -> Tuple[str, str]:
        """Extract and clean article content."""
        content_elem = soup.select_one(selectors.content)
        if not content_elem:
            for sel in ['article', '.article-body', '.post-content', 'main']:
                content_elem = soup.select_one(sel)
                if content_elem:
                    break
        
        if not content_elem:
            return ("", "")
        
        content = BeautifulSoup(str(content_elem), 'lxml')
        
        for tag_name in self.REMOVE_TAGS:
            for tag in content.find_all(tag_name):
                tag.decompose()
        
        for comment in content.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        
        for elem in content.find_all(class_=True):
            if not hasattr(elem, 'get'):
                continue
            
            try:
                classes_list = elem.get('class', [])
                if not classes_list:
                    continue
                classes = ' '.join(classes_list)
            except:
                continue
                
            for pattern in self.REMOVE_PATTERNS:
                if pattern.search(classes):
                    elem.decompose()
                    break
        
        html_content = str(content)
        text_content = content.get_text(separator='\n', strip=True)
        text_content = re.sub(r'\n{3,}', '\n\n', text_content)
        
        return (html_content, text_content)
    
    def _extract_images(self, soup: BeautifulSoup, base_url: str, 
                       selectors: SelectorSet) -> List[str]:
        """Extract image URLs."""
        content_elem = soup.select_one(selectors.content)
        if not content_elem:
            content_elem = soup
        
        images = []
        for img in content_elem.find_all('img'):
            src = img.get('data-src') or img.get('src')
            if not src or src.startswith('data:'):
                continue
            
            src = urljoin(base_url, src)
            
            if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                images.append(src)
        
        return images
    
    def _extract_category(self, soup: BeautifulSoup) -> str:
        """Extract article category."""
        meta = soup.find('meta', property='article:section')
        if meta and meta.get('content'):
            return meta['content']
        
        breadcrumb = soup.select_one('.breadcrumb a:last-child')
        if breadcrumb:
            return breadcrumb.get_text(strip=True)
        
        return ""


# Singleton
_parser: Optional[ArticleParser] = None


def get_parser() -> ArticleParser:
    """Get parser singleton."""
    global _parser
    if _parser is None:
        _parser = ArticleParser()
    return _parser
