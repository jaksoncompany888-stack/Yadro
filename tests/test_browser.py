"""
Tests for Browser Tool (Layer 4)

Тесты для Playwright браузера.
Интеграционные тесты отключены - требуют реального браузера и могут быть нестабильны.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from app.tools.browser import BrowserTool, SearchResult, web_search


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


# Интеграционные тесты отключены - требуют реального браузера
# и могут быть нестабильны из-за капчи Google
