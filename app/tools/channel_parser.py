"""
Channel Parser - парсинг постов из Telegram каналов через веб
"""
import re
import requests
from typing import List
from dataclasses import dataclass
from bs4 import BeautifulSoup


@dataclass
class ChannelPost:
    text: str
    views: int
    date: str
    url: str


class ChannelParser:
    """Парсит публичные Telegram каналы через t.me/s/"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def parse_channel(self, channel: str, limit: int = 10) -> List[ChannelPost]:
        """
        Парсит посты из канала.
        channel: @username или username
        """
        username = channel.replace("@", "").replace("https://t.me/", "")
        url = f"https://t.me/s/{username}"

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            raise Exception(f"Не удалось загрузить канал: {e}")

        soup = BeautifulSoup(resp.text, 'html.parser')
        messages = soup.select('.tgme_widget_message')

        if not messages:
            raise Exception("Посты не найдены. Канал приватный или не существует.")

        posts = []
        for msg in messages[:limit]:
            # Текст
            text_el = msg.select_one('.tgme_widget_message_text')
            text = text_el.get_text(strip=True) if text_el else ""

            # Просмотры
            views_el = msg.select_one('.tgme_widget_message_views')
            views_str = views_el.get_text(strip=True) if views_el else "0"
            views = self._parse_views(views_str)

            # Дата
            date_el = msg.select_one('time')
            date = date_el.get('datetime', '') if date_el else ""

            # URL
            link_el = msg.select_one('.tgme_widget_message_date')
            post_url = link_el.get('href', '') if link_el else ""

            if text:
                posts.append(ChannelPost(
                    text=text[:500],
                    views=views,
                    date=date,
                    url=post_url
                ))

        return posts

    def _parse_views(self, views_str: str) -> int:
        """Парсит строку просмотров: 1.5K -> 1500"""
        views_str = views_str.upper().strip()
        if 'K' in views_str:
            return int(float(views_str.replace('K', '')) * 1000)
        elif 'M' in views_str:
            return int(float(views_str.replace('M', '')) * 1000000)
        else:
            return int(re.sub(r'[^\d]', '', views_str) or 0)

    def get_top_posts(self, channel: str, limit: int = 5) -> List[ChannelPost]:
        """Получить топ постов по просмотрам"""
        posts = self.parse_channel(channel, limit=20)
        return sorted(posts, key=lambda x: x.views, reverse=True)[:limit]

    def get_recent_posts(self, channel: str, limit: int = 5) -> List[ChannelPost]:
        """Получить последние посты (по времени, не по просмотрам)"""
        return self.parse_channel(channel, limit=limit)

    def stop(self):
        """Для совместимости"""
        pass
