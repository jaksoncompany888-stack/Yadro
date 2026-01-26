"""
Yadro v0 - Web Search Tool

Поиск в интернете через DuckDuckGo (бесплатно, без API ключа).
"""
import urllib.request
import urllib.parse
import json
import re
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class SearchResult:
    """Результат поиска."""
    title: str
    url: str
    snippet: str


def web_search(query: str, max_results: int = 5) -> List[SearchResult]:
    """
    Поиск в интернете через DuckDuckGo.
    
    Args:
        query: Поисковый запрос
        max_results: Максимум результатов
        
    Returns:
        Список SearchResult
    """
    # DuckDuckGo HTML search (no API key needed)
    encoded_query = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
    except Exception as e:
        return []
    
    # Parse results (simple regex parsing)
    results = []
    
    # Find result blocks
    pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>.*?<a class="result__snippet"[^>]*>([^<]+)</a>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    for match in matches[:max_results]:
        url, title, snippet = match
        # Clean up
        title = re.sub(r'<[^>]+>', '', title).strip()
        snippet = re.sub(r'<[^>]+>', '', snippet).strip()
        
        if title and url:
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=snippet,
            ))
    
    return results


def search_and_summarize(query: str) -> str:
    """
    Поиск и форматирование результатов для LLM.
    
    Args:
        query: Поисковый запрос
        
    Returns:
        Форматированная строка с результатами
    """
    results = web_search(query, max_results=5)
    
    if not results:
        return f"Поиск по запросу '{query}' не дал результатов."
    
    lines = [f"Результаты поиска по запросу '{query}':\n"]
    
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r.title}**")
        lines.append(f"   {r.snippet}")
        lines.append(f"   URL: {r.url}")
        lines.append("")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Тест
    results = web_search("S&P 500 Trump news today")
    for r in results:
        print(f"- {r.title}")
        print(f"  {r.snippet[:100]}...")
        print()
