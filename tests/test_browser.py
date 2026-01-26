"""
Tests for Browser Tool (Layer 4)

Тесты для Playwright браузера.
Некоторые тесты требуют интернет и запускают реальный браузер.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from app.tools.browser import BrowserTool, SearchResult, web_search, search_and_summarize


class TestSearchResult:
    """Тесты для SearchResult."""
    
    def test_create_search_result(self):
        """Создание результата поиска."""
        result = SearchResult(
            title="Test Title",
            url="https://example.com",
            snippet="Test snippet"
        )
        
        assert result.title == "Test Title"
        assert result.url == "https://example.com"
        assert result.snippet == "Test snippet"


class TestBrowserTool:
    """Тесты для BrowserTool."""
    
    def test_init_headless(self):
        """Инициализация в headless режиме."""
        browser = BrowserTool(headless=True)
        assert browser.headless is True
        assert browser._playwright is None
        assert browser._browser is None
    
    def test_init_visible(self):
        """Инициализация с видимым окном."""
        browser = BrowserTool(headless=False)
        assert browser.headless is False


class TestBrowserToolIntegration:
    """
    Интеграционные тесты с реальным браузером.
    
    Эти тесты запускают Playwright и требуют интернет.
    Помечены маркером 'integration' для возможности пропуска.
    """
    
    @pytest.mark.integration
    def test_start_stop(self):
        """Запуск и остановка браузера."""
        browser = BrowserTool(headless=True)
        browser.start()
        
        assert browser._playwright is not None
        assert browser._browser is not None
        assert browser._page is not None
        
        browser.stop()
        
        assert browser._playwright is None
        assert browser._browser is None
    
    @pytest.mark.integration
    def test_goto(self):
        """Переход на страницу."""
        browser = BrowserTool(headless=True)
        try:
            browser.start()
            browser.goto("https://example.com")
            
            text = browser.get_text()
            assert "Example Domain" in text
        finally:
            browser.stop()
    
    @pytest.mark.integration
    def test_screenshot(self, tmp_path):
        """Создание скриншота."""
        browser = BrowserTool(headless=True)
        try:
            browser.start()
            browser.goto("https://example.com")
            
            path = str(tmp_path / "test.png")
            result = browser.screenshot(path)
            
            assert result == path
            # Проверяем что файл создан
            import os
            assert os.path.exists(path)
        finally:
            browser.stop()
    
    @pytest.mark.integration
    def test_search_google(self):
        """Поиск в Google."""
        browser = BrowserTool(headless=True)
        try:
            browser.start()
            results = browser.search_google("python programming")
            
            assert len(results) > 0
            assert all(isinstance(r, SearchResult) for r in results)
            assert all(r.title for r in results)
            assert all(r.url for r in results)
        finally:
            browser.stop()


class TestWebSearchFunction:
    """Тесты для функции web_search."""
    
    @pytest.mark.integration
    def test_web_search_google(self):
        """Быстрый поиск через Google."""
        results = web_search("test query", engine="google", headless=True)
        
        assert isinstance(results, list)
        # Может быть пустым если Google блокирует, но не должен падать
    
    @pytest.mark.integration
    def test_web_search_returns_search_results(self):
        """Результаты правильного типа."""
        results = web_search("python", headless=True)
        
        for r in results:
            assert isinstance(r, SearchResult)


class TestSearchAndSummarize:
    """Тесты для search_and_summarize."""
    
    def test_empty_results(self):
        """Пустые результаты."""
        with patch('app.tools.browser.web_search', return_value=[]):
            result = search_and_summarize("test query")
            assert "не дал результатов" in result
    
    def test_formats_results(self):
        """Форматирование результатов."""
        mock_results = [
            SearchResult(title="Title 1", url="http://url1.com", snippet="Snippet 1"),
            SearchResult(title="Title 2", url="http://url2.com", snippet="Snippet 2"),
        ]
        
        with patch('app.tools.browser.web_search', return_value=mock_results):
            result = search_and_summarize("test")
            
            assert "Title 1" in result
            assert "Title 2" in result
            assert "url1.com" in result
            assert "url2.com" in result
