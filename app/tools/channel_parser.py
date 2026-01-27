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
    reactions: int = 0  # общее кол-во реакций
    forwards: int = 0   # репосты


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

            # Реакции (сумма всех)
            reactions = 0
            reaction_els = msg.select('.tgme_reaction')
            for r in reaction_els:
                # Текст внутри span содержит число после эмодзи
                r_text = r.get_text(strip=True)
                # Извлекаем только цифры из текста
                nums = re.findall(r'\d+', r_text)
                if nums:
                    reactions += int(nums[-1])  # Берём последнее число (количество)

            # Репосты/форварды
            forwards = 0
            forward_el = msg.select_one('.tgme_widget_message_forwards')
            if forward_el:
                forwards = self._parse_views(forward_el.get_text(strip=True))

            if text:
                posts.append(ChannelPost(
                    text=text[:500],
                    views=views,
                    date=date,
                    url=post_url,
                    reactions=reactions,
                    forwards=forwards
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

    def get_channel_info(self, channel: str) -> dict:
        """Получить информацию о канале: название, подписчики, описание"""
        username = channel.replace("@", "").replace("https://t.me/", "")
        url = f"https://t.me/s/{username}"

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception:
            return {"subscribers": 0, "title": username, "description": ""}

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Название канала
        title_el = soup.select_one('.tgme_channel_info_header_title')
        title = title_el.get_text(strip=True) if title_el else username

        # Подписчики
        subs_el = soup.select_one('.tgme_channel_info_counter')
        subscribers = 0
        if subs_el:
            subs_text = subs_el.get_text(strip=True)
            # Убираем слова типа "subscribers", "members" и т.д.
            subs_num = re.sub(r'[^\d.KMkm]', '', subs_text.split()[0] if ' ' in subs_text else subs_text)
            subscribers = self._parse_views(subs_num) if subs_num else 0

        # Описание
        desc_el = soup.select_one('.tgme_channel_info_description')
        description = desc_el.get_text(strip=True) if desc_el else ""

        return {
            "title": title,
            "subscribers": subscribers,
            "description": description[:200]
        }

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
