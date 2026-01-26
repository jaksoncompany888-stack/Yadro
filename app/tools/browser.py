"""
Yadro v0 - Browser Tool (Playwright)

Умный браузер с human-in-the-loop.
"""
from playwright.sync_api import sync_playwright
from typing import List, Optional, Callable
from dataclasses import dataclass
import time
import random


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class BrowserTool:
    """
    Умный браузер для агента.
    
    Фичи:
    - Закрытие куки/попапов
    - Скролл для подгрузки
    - Передача управления юзеру
    - Скриншоты в процессе
    """
    
    # Селекторы для закрытия мусора
    COOKIE_SELECTORS = [
        'button:has-text("Accept")',
        'button:has-text("Принять")',
        'button:has-text("Accept all")',
        'button:has-text("Принять все")',
        'button:has-text("Agree")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("Понятно")',
        '[class*="cookie"] button',
        '[id*="cookie"] button',
        '[class*="consent"] button',
        '[class*="gdpr"] button',
    ]
    
    POPUP_SELECTORS = [
        'button[aria-label="Close"]',
        'button[aria-label="Закрыть"]',
        '[class*="close"]',
        '[class*="dismiss"]',
        '[class*="modal"] button:has-text("×")',
        '[class*="popup"] button',
        '.overlay-close',
    ]
    
    AD_SELECTORS = [
        '[class*="advertisement"]',
        '[class*="ad-"]',
        '[id*="ad-"]',
        '[class*="banner"]',
        'iframe[src*="ads"]',
    ]
    
    def __init__(self, headless: bool = True, on_need_human: Optional[Callable] = None):
        """
        Args:
            headless: Без окна браузера
            on_need_human: Callback когда нужен человек
        """
        self.headless = headless
        self.on_need_human = on_need_human or self._default_human_callback
        self._playwright = None
        self._browser = None
        self._page = None
        self._screenshots = []
    
    def _default_human_callback(self, reason: str, screenshot_path: str) -> bool:
        """Дефолтный callback — спрашивает в консоли."""
        print(f"\n🙋 Нужна помощь: {reason}")
        print(f"   Скриншот: {screenshot_path}")
        response = input("   Готово? (y/n): ").strip().lower()
        return response == 'y'
    
    def start(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
        )
        context = self._browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='ru-RU'
        )
        self._page = context.new_page()
        return self
    
    def stop(self):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
    
    def _human_type(self, selector: str, text: str):
        """Печатаем как человек."""
        self._page.click(selector)
        for char in text:
            self._page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.12))
    
    def _human_delay(self, min_s=0.5, max_s=1.5):
        time.sleep(random.uniform(min_s, max_s))
    
    def close_cookies(self):
        """Закрыть куки-баннеры."""
        for selector in self.COOKIE_SELECTORS:
            try:
                btn = self._page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    self._human_delay(0.3, 0.7)
                    return True
            except:
                continue
        return False
    
    def close_popups(self):
        """Закрыть попапы и модалки."""
        closed = 0
        for selector in self.POPUP_SELECTORS:
            try:
                btns = self._page.query_selector_all(selector)
                for btn in btns:
                    if btn.is_visible():
                        btn.click()
                        closed += 1
                        self._human_delay(0.2, 0.5)
            except:
                continue
        return closed
    
    def hide_ads(self):
        """Скрыть рекламу через CSS."""
        for selector in self.AD_SELECTORS:
            try:
                self._page.evaluate(f'''
                    document.querySelectorAll('{selector}').forEach(el => el.style.display = 'none')
                ''')
            except:
                continue
    
    def clean_page(self):
        """Полная очистка страницы от мусора."""
        self.close_cookies()
        self.close_popups()
        self.hide_ads()
    
    def scroll_down(self, times: int = 3):
        """Скролл вниз для подгрузки контента."""
        for _ in range(times):
            self._page.keyboard.press("PageDown")
            self._human_delay(0.5, 1)
    
    def scroll_to_bottom(self):
        """Скролл до конца страницы."""
        self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        self._human_delay(1, 2)
    
    def screenshot(self, name: str = None) -> str:
        """Сделать скриншот."""
        name = name or f"screenshot_{len(self._screenshots)}.png"
        path = f"{name}"
        self._page.screenshot(path=path)
        self._screenshots.append(path)
        return path
    
    def check_captcha(self) -> bool:
        """Проверка на капчу."""
        captcha_signs = [
            'iframe[src*="recaptcha"]',
            'iframe[src*="captcha"]',
            '[class*="captcha"]',
            'img[src*="captcha"]',
            'text=Подтвердите, что вы не робот',
            'text=I am not a robot',
        ]
        for selector in captcha_signs:
            try:
                if self._page.query_selector(selector):
                    return True
            except:
                continue
        return False
    
    def check_login_required(self) -> bool:
        """Проверка требуется ли логин."""
        login_signs = [
            'input[type="password"]',
            'button:has-text("Войти")',
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            'a:has-text("Войти")',
            '[class*="login"]',
            '[class*="signin"]',
        ]
        for selector in login_signs:
            try:
                el = self._page.query_selector(selector)
                if el and el.is_visible():
                    return True
            except:
                continue
        return False
    
    def request_human_help(self, reason: str) -> bool:
        """Запросить помощь человека."""
        screenshot_path = self.screenshot(f"need_help_{len(self._screenshots)}.png")
        return self.on_need_human(reason, screenshot_path)
    
    def goto(self, url: str, clean: bool = True) -> bool:
        """
        Переход на страницу с обработкой проблем.
        
        Returns:
            True если успешно, False если нужна помощь
        """
        self._page.goto(url, timeout=15000)
        self._human_delay(1, 2)
        
        if clean:
            self.clean_page()
        
        # Проверяем капчу
        if self.check_captcha():
            if not self.request_human_help("Обнаружена капча. Пройди её."):
                return False
        
        # Проверяем логин
        if self.check_login_required():
            if not self.request_human_help("Требуется авторизация. Залогинься."):
                return False
        
        return True
    
    def search_google(self, query: str) -> List[SearchResult]:
        """Поиск в Google."""
        self._page.goto("https://www.google.com/search?hl=ru")
        self._human_delay(1, 2)
        
        self.close_cookies()
        
        self._human_type('textarea[name="q"]', query)
        self._human_delay(0.3, 0.7)
        self._page.keyboard.press("Enter")
        self._page.wait_for_load_state("networkidle")
        self._human_delay(1, 2)
        
        if self.check_captcha():
            if not self.request_human_help("Google показал капчу"):
                return []
        
        self.screenshot("google_results.png")
        
        results = []
        items = self._page.query_selector_all('a:has(h3)')
        
        for item in items[:7]:
            try:
                title_el = item.query_selector("h3")
                url = item.get_attribute("href")
                
                if title_el and url and url.startswith("http"):
                    results.append(SearchResult(
                        title=title_el.inner_text(),
                        url=url,
                        snippet=""
                    ))
            except:
                continue
        
        return results
    
    def read_page(self, url: str, scroll: bool = True) -> str:
        """
        Прочитать страницу целиком.
        
        Args:
            url: URL страницы
            scroll: Скроллить для подгрузки
            
        Returns:
            Текст страницы
        """
        success = self.goto(url)
        if not success:
            return ""
        
        if scroll:
            self.scroll_down(2)
        
        self.clean_page()
        
        # Получаем текст без лишнего
        text = self._page.evaluate('''
            () => {
                // Убираем скрипты, стили, навигацию
                const remove = ['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe'];
                remove.forEach(tag => {
                    document.querySelectorAll(tag).forEach(el => el.remove());
                });
                return document.body.innerText;
            }
        ''')
        
        return text[:5000]  # Лимит символов
    
    def get_screenshots(self) -> List[str]:
        """Получить все скриншоты сессии."""
        return self._screenshots


def web_search(query: str, headless: bool = True) -> List[SearchResult]:
    """Быстрый поиск."""
    browser = BrowserTool(headless=headless)
    try:
        browser.start()
        return browser.search_google(query)
    finally:
        browser.stop()


if __name__ == "__main__":
    print("Тест умного браузера...\n")
    
    browser = BrowserTool(headless=False)
    browser.start()
    
    try:
        # Поиск
        print("🔍 Ищу...")
        results = browser.search_google("Билеты в театр Москва")
        print(f"   Найдено: {len(results)}")
        
        if results:
            # Читаем первую страницу
            print(f"\n📖 Читаю: {results[0].title}")
            text = browser.read_page(results[0].url)
            print(f"   Получено {len(text)} символов")
            print(f"   Превью: {text[:200]}...")
        
        print(f"\n📸 Скриншоты: {browser.get_screenshots()}")
        
    finally:
        browser.stop()
