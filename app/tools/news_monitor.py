"""
News Monitor - мониторинг новостных источников
"""
import re
import requests
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime
from bs4 import BeautifulSoup


@dataclass
class NewsItem:
    title: str
    summary: str
    url: str
    source: str
    published: Optional[str] = None


class NewsMonitor:
    """Мониторинг новостей из разных источников"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def fetch_techcrunch(self, limit: int = 5) -> List[NewsItem]:
        """TechCrunch - tech новости"""
        try:
            resp = self.session.get('https://techcrunch.com/feed/', timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')
            items = []
            for item in soup.select('item')[:limit]:
                title = item.select_one('title').text if item.select_one('title') else ''
                desc = item.select_one('description').text if item.select_one('description') else ''
                link = item.select_one('link').text if item.select_one('link') else ''
                pub = item.select_one('pubDate').text if item.select_one('pubDate') else ''

                # Чистим HTML из description
                desc_clean = BeautifulSoup(desc, 'html.parser').get_text()[:300]

                items.append(NewsItem(
                    title=title,
                    summary=desc_clean,
                    url=link,
                    source='TechCrunch',
                    published=pub
                ))
            return items
        except Exception as e:
            return []

    def fetch_theverge(self, limit: int = 5) -> List[NewsItem]:
        """The Verge - tech новости"""
        try:
            resp = self.session.get('https://www.theverge.com/rss/index.xml', timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')
            items = []
            for entry in soup.select('entry')[:limit]:
                title = entry.select_one('title').text if entry.select_one('title') else ''
                summary = entry.select_one('summary').text if entry.select_one('summary') else ''
                link = entry.select_one('link')
                url = link.get('href', '') if link else ''
                pub = entry.select_one('published').text if entry.select_one('published') else ''

                summary_clean = BeautifulSoup(summary, 'html.parser').get_text()[:300]

                items.append(NewsItem(
                    title=title,
                    summary=summary_clean,
                    url=url,
                    source='The Verge',
                    published=pub
                ))
            return items
        except Exception as e:
            return []

    def fetch_hackernews(self, limit: int = 5) -> List[NewsItem]:
        """Hacker News - топ новости"""
        try:
            resp = self.session.get('https://hacker-news.firebaseio.com/v0/topstories.json', timeout=10)
            story_ids = resp.json()[:limit]

            items = []
            for sid in story_ids:
                story = self.session.get(f'https://hacker-news.firebaseio.com/v0/item/{sid}.json', timeout=5).json()
                if story and story.get('title'):
                    items.append(NewsItem(
                        title=story.get('title', ''),
                        summary=story.get('text', '')[:300] if story.get('text') else '',
                        url=story.get('url', f"https://news.ycombinator.com/item?id={sid}"),
                        source='Hacker News',
                        published=None
                    ))
            return items
        except Exception as e:
            return []

    def fetch_producthunt(self, limit: int = 5) -> List[NewsItem]:
        """Product Hunt - новые продукты (Atom формат)"""
        try:
            resp = self.session.get('https://www.producthunt.com/feed', timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')
            items = []
            for entry in soup.select('entry')[:limit]:
                title = entry.select_one('title').text if entry.select_one('title') else ''
                content = entry.select_one('content').text if entry.select_one('content') else ''
                link = entry.select_one('link')
                url = link.get('href', '') if link else ''

                content_clean = BeautifulSoup(content, 'html.parser').get_text()[:300]

                items.append(NewsItem(
                    title=title,
                    summary=content_clean,
                    url=url,
                    source='Product Hunt'
                ))
            return items
        except Exception as e:
            return []

    def fetch_all(self, limit_per_source: int = 3) -> List[NewsItem]:
        """Собрать новости из всех источников"""
        all_news = []
        all_news.extend(self.fetch_techcrunch(limit_per_source))
        all_news.extend(self.fetch_theverge(limit_per_source))
        all_news.extend(self.fetch_hackernews(limit_per_source))
        all_news.extend(self.fetch_producthunt(limit_per_source))
        return all_news

    def fetch_custom_rss(self, url: str, source_name: str = "", limit: int = 5) -> List[NewsItem]:
        """Парсинг кастомного RSS фида"""
        try:
            resp = self.session.get(url, timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')

            items = []

            # Пробуем RSS формат
            for item in soup.select('item')[:limit]:
                title = item.select_one('title')
                desc = item.select_one('description')
                link = item.select_one('link')

                if title:
                    desc_text = desc.get_text(strip=True)[:300] if desc else ''
                    desc_clean = BeautifulSoup(desc_text, 'html.parser').get_text()

                    items.append(NewsItem(
                        title=title.get_text(strip=True),
                        summary=desc_clean,
                        url=link.get_text(strip=True) if link else url,
                        source=source_name or url
                    ))

            # Если RSS пустой, пробуем Atom формат
            if not items:
                for entry in soup.select('entry')[:limit]:
                    title = entry.select_one('title')
                    summary = entry.select_one('summary') or entry.select_one('content')
                    link = entry.select_one('link')

                    if title:
                        summary_text = summary.get_text(strip=True)[:300] if summary else ''
                        summary_clean = BeautifulSoup(summary_text, 'html.parser').get_text()

                        items.append(NewsItem(
                            title=title.get_text(strip=True),
                            summary=summary_clean,
                            url=link.get('href', url) if link else url,
                            source=source_name or url
                        ))

            return items
        except Exception as e:
            return []

    def search_duckduckgo(self, query: str, limit: int = 5) -> List[NewsItem]:
        """Поиск через DuckDuckGo (без капчи)"""
        try:
            url = f"https://html.duckduckgo.com/html/?q={query}"
            resp = self.session.get(url, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')

            items = []
            for result in soup.select('.result')[:limit]:
                title_el = result.select_one('.result__title')
                snippet_el = result.select_one('.result__snippet')
                link_el = result.select_one('.result__url')

                if title_el:
                    title = title_el.get_text(strip=True)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ''
                    url = link_el.get('href', '') if link_el else ''

                    items.append(NewsItem(
                        title=title,
                        summary=snippet[:300],
                        url=url,
                        source='DuckDuckGo'
                    ))
            return items
        except Exception as e:
            return []
