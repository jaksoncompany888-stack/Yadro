"""
SMM Tools - инструменты для SMM агента

Регистрируются в ToolRegistry и вызываются через StepExecutor.
"""
from typing import Dict, Any, List, Optional
from .registry import registry
from .models import ToolImpact


def register_smm_tools(
    channel_parser=None,
    news_monitor=None,
    memory_service=None,
    llm_service=None,
):
    """
    Регистрация SMM tools в глобальном registry.

    Args:
        channel_parser: ChannelParser instance
        news_monitor: NewsMonitor instance
        memory_service: MemoryService instance
        llm_service: LLMService instance
    """

    # ==================== PARSE CHANNEL ====================
    def parse_channel(channel: str, limit: int = 10, top: bool = True) -> Dict[str, Any]:
        """Парсинг постов из Telegram канала"""
        if channel_parser is None:
            return {"error": "channel_parser not configured"}

        try:
            if top:
                posts = channel_parser.get_top_posts(channel, limit=limit)
            else:
                posts = channel_parser.get_recent_posts(channel, limit=limit)

            return {
                "channel": channel,
                "posts": [
                    {"text": p.text, "views": p.views, "date": p.date}
                    for p in posts
                ],
                "count": len(posts)
            }
        except Exception as e:
            return {"error": str(e), "channel": channel}

    registry.register(
        name="parse_channel",
        handler=parse_channel,
        description="Парсинг постов из Telegram канала",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_analyze", "smm_generate"],
        parameters={
            "channel": {"type": "string", "required": True},
            "limit": {"type": "integer", "default": 10},
            "top": {"type": "boolean", "default": True},
        }
    )

    # ==================== WEB SEARCH ====================
    def web_search(query: str, limit: int = 5) -> Dict[str, Any]:
        """Поиск в интернете через DuckDuckGo"""
        if news_monitor is None:
            return {"error": "news_monitor not configured"}

        try:
            results = news_monitor.search_duckduckgo(query, limit=limit)
            return {
                "query": query,
                "results": [
                    {"title": r.title, "summary": r.summary, "url": r.url}
                    for r in results
                ],
                "count": len(results)
            }
        except Exception as e:
            return {"error": str(e), "query": query}

    registry.register(
        name="web_search",
        handler=web_search,
        description="Поиск актуальной информации в интернете",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_generate", "research"],
        parameters={
            "query": {"type": "string", "required": True},
            "limit": {"type": "integer", "default": 5},
        }
    )

    # ==================== FETCH NEWS ====================
    def fetch_news(limit_per_source: int = 3) -> Dict[str, Any]:
        """Получение новостей из всех источников"""
        if news_monitor is None:
            return {"error": "news_monitor not configured"}

        try:
            news = news_monitor.fetch_all(limit_per_source=limit_per_source)
            return {
                "news": [
                    {"title": n.title, "summary": n.summary, "source": n.source, "url": n.url}
                    for n in news
                ],
                "count": len(news)
            }
        except Exception as e:
            return {"error": str(e)}

    registry.register(
        name="fetch_news",
        handler=fetch_news,
        description="Получение свежих новостей",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_generate"],
        parameters={
            "limit_per_source": {"type": "integer", "default": 3},
        }
    )

    # ==================== MEMORY SEARCH ====================
    def memory_search(user_id: int, query: str, limit: int = 5) -> Dict[str, Any]:
        """Поиск в памяти пользователя (FTS5)"""
        if memory_service is None:
            return {"error": "memory_service not configured"}

        try:
            results = memory_service.search(user_id, query, limit=limit)
            return {
                "query": query,
                "results": [
                    {"content": r.content, "type": r.memory_type.value if hasattr(r.memory_type, 'value') else str(r.memory_type)}
                    for r in results
                ],
                "count": len(results)
            }
        except Exception as e:
            return {"error": str(e), "query": query}

    registry.register(
        name="memory_search",
        handler=memory_search,
        description="Поиск похожего контента в памяти",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_generate", "smm_analyze"],
        parameters={
            "user_id": {"type": "integer", "required": True},
            "query": {"type": "string", "required": True},
            "limit": {"type": "integer", "default": 5},
        }
    )

    # ==================== MEMORY STORE ====================
    def memory_store(
        user_id: int,
        content: str,
        memory_type: str = "context",
        importance: float = 0.7,
        metadata: Dict = None
    ) -> Dict[str, Any]:
        """Сохранение в память пользователя с очисткой старых данных"""
        if memory_service is None:
            return {"error": "memory_service not configured"}

        try:
            from app.memory import MemoryType
            type_map = {
                "fact": MemoryType.FACT,
                "decision": MemoryType.DECISION,
                "context": MemoryType.CONTEXT,
                "feedback": MemoryType.FEEDBACK,
            }
            mem_type = type_map.get(memory_type, MemoryType.CONTEXT)

            # Если это анализ канала — сначала удаляем старый анализ
            if content.startswith("Стиль канала") and metadata and metadata.get("channel"):
                channel = metadata["channel"]
                # Удаляем старые записи "Стиль канала {channel}..."
                memory_service.db.execute(
                    "DELETE FROM memory_items WHERE user_id = ? AND content LIKE ?",
                    (user_id, f"Стиль канала {channel}%")
                )
                print(f"[Memory] Очищен старый анализ {channel}")

            memory_service.store(
                user_id=user_id,
                content=content,
                memory_type=mem_type,
                importance=importance,
                metadata=metadata
            )
            return {"success": True, "content": content[:100], "version": metadata.get("analysis_version") if metadata else None}
        except Exception as e:
            return {"error": str(e)}

    registry.register(
        name="memory_store",
        handler=memory_store,
        description="Сохранение информации в память",
        impact=ToolImpact.MEDIUM,
        allowed_task_types=["smm", "smm_analyze", "smm_generate"],
        parameters={
            "user_id": {"type": "integer", "required": True},
            "content": {"type": "string", "required": True},
            "memory_type": {"type": "string", "default": "context"},
            "importance": {"type": "number", "default": 0.7},
            "metadata": {"type": "object", "default": None},
        }
    )

    # ==================== COMPUTE CHANNEL METRICS ====================
    def compute_channel_metrics(posts: List[Dict]) -> Dict[str, Any]:
        """
        Вычисление метрик канала БЕЗ LLM — чистая логика.

        Анализирует:
        - Длина постов
        - Эмодзи
        - Хештеги
        - Структура (списки, абзацы)
        - Топ слова
        - Паттерны начала/конца
        """
        import re
        from collections import Counter

        if not posts:
            return {"error": "no posts to analyze"}

        # Фильтруем рекламу
        ad_markers = ['#реклама', '#ad', 'промокод', 'скидка', 'купить']
        organic = [p for p in posts if not any(m in p.get('text', '').lower() for m in ad_markers)]

        if not organic:
            organic = posts[:5]

        texts = [p.get('text', '') for p in organic]

        # === МЕТРИКИ ===

        # 1. Длина постов
        lengths = [len(t) for t in texts]
        avg_length = sum(lengths) // len(lengths) if lengths else 0
        length_category = "короткие" if avg_length < 300 else "средние" if avg_length < 800 else "длинные"

        # 2. Эмодзи
        emoji_pattern = re.compile(r'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]')
        emoji_counts = [len(emoji_pattern.findall(t)) for t in texts]
        avg_emoji = sum(emoji_counts) / len(emoji_counts) if emoji_counts else 0
        emoji_style = "много эмодзи" if avg_emoji > 3 else "мало эмодзи" if avg_emoji > 0 else "без эмодзи"

        # 3. Хештеги
        hashtag_pattern = re.compile(r'#\w+')
        hashtag_counts = [len(hashtag_pattern.findall(t)) for t in texts]
        avg_hashtags = sum(hashtag_counts) / len(hashtag_counts) if hashtag_counts else 0
        all_hashtags = []
        for t in texts:
            all_hashtags.extend(hashtag_pattern.findall(t))
        top_hashtags = [h for h, _ in Counter(all_hashtags).most_common(5)]

        # 4. Структура
        has_lists = sum(1 for t in texts if re.search(r'(^|\n)[•\-\d]\s', t)) / len(texts) > 0.3
        has_paragraphs = sum(1 for t in texts if t.count('\n\n') >= 2) / len(texts) > 0.3
        structure = []
        if has_lists:
            structure.append("списки")
        if has_paragraphs:
            structure.append("абзацы")
        if not structure:
            structure.append("сплошной текст")

        # 5. Стартовые паттерны (hooks)
        first_lines = [t.split('\n')[0][:50] for t in texts if t]
        hook_patterns = []
        question_hooks = sum(1 for l in first_lines if '?' in l)
        if question_hooks >= 2:
            hook_patterns.append("вопросы")
        emoji_hooks = sum(1 for l in first_lines if emoji_pattern.search(l))
        if emoji_hooks >= 2:
            hook_patterns.append("эмодзи в начале")
        caps_hooks = sum(1 for l in first_lines if l.isupper() or l[:10].isupper())
        if caps_hooks >= 2:
            hook_patterns.append("КАПС")

        # 6. Концовки (CTA)
        last_lines = [t.strip().split('\n')[-1] for t in texts if t]
        cta_keywords = ['подписы', 'ставь', 'пиши', 'делись', 'репост', 'комент', 'ссылк']
        has_cta = sum(1 for l in last_lines if any(k in l.lower() for k in cta_keywords))
        cta_style = "есть CTA" if has_cta >= 2 else "без CTA"

        # 7. Топ слова (исключая стоп-слова)
        stop_words = {'и', 'в', 'на', 'с', 'что', 'это', 'как', 'а', 'не', 'но', 'для', 'по', 'к', 'из', 'у', 'о', 'же', 'то', 'все', 'так', 'его', 'от', 'они', 'вы', 'мы', 'я', 'бы', 'он', 'она', 'было', 'быть', 'или', 'при', 'уже', 'если', 'их', 'ее', 'её', 'только', 'когда', 'этот', 'эта', 'эти', 'вот', 'тут', 'там', 'ты', 'за'}
        all_words = []
        for t in texts:
            words = re.findall(r'\b[а-яА-ЯёЁa-zA-Z]{4,}\b', t.lower())
            all_words.extend([w for w in words if w not in stop_words])
        top_words = [w for w, _ in Counter(all_words).most_common(10)]

        # 8. Просмотры
        views = [p.get('views', 0) for p in organic if p.get('views')]
        avg_views = sum(views) // len(views) if views else 0
        max_views = max(views) if views else 0
        min_views = min(views) if views else 0

        # 9. Реакции
        reactions = [p.get('reactions', 0) for p in organic]
        avg_reactions = sum(reactions) // len(reactions) if reactions else 0
        total_reactions = sum(reactions)

        # 10. Репосты
        forwards = [p.get('forwards', 0) for p in organic]
        avg_forwards = sum(forwards) // len(forwards) if forwards else 0

        # 11. Engagement rate (реакции + репосты / просмотры)
        engagement = 0
        if avg_views > 0:
            engagement = round((avg_reactions + avg_forwards) / avg_views * 100, 2)

        # === АВТОМАТИЧЕСКИЙ РАСЧЁТ TEMPERATURE ===
        # На основе метрик определяем тип канала и оптимальную температуру
        #
        # Аналитика/новости: длинные посты, мало эмодзи → 0.3 (точность)
        # Экспертный: средняя длина, структура → 0.5 (баланс)
        # Лайфстайл/авторский: короткие, эмодзи, вопросы → 0.7 (креатив)

        recommended_temperature = 0.5  # default
        content_type = "экспертный"  # default

        # Аналитика: длинные + мало эмодзи + без вопросов
        if avg_length > 500 and avg_emoji < 1.5 and "вопросы" not in hook_patterns:
            recommended_temperature = 0.3
            content_type = "аналитический"
        # Развлекательный/лайфстайл: короткие + эмодзи или вопросы
        elif avg_length < 300 and (avg_emoji > 1.5 or "вопросы" in hook_patterns):
            recommended_temperature = 0.7
            content_type = "лайфстайл"
        # Новостной: любая длина + нет эмодзи + нет CTA
        elif avg_emoji < 0.5 and cta_style == "без CTA":
            recommended_temperature = 0.35
            content_type = "новостной"
        # Авторский блог: эмодзи + CTA
        elif avg_emoji > 2 and cta_style == "есть CTA":
            recommended_temperature = 0.6
            content_type = "авторский"

        return {
            "posts_analyzed": len(organic),
            "metrics": {
                "avg_length": avg_length,
                "length_category": length_category,
                "avg_emoji": round(avg_emoji, 1),
                "emoji_style": emoji_style,
                "avg_hashtags": round(avg_hashtags, 1),
                "top_hashtags": top_hashtags,
                "structure": structure,
                "hook_patterns": hook_patterns or ["без явных паттернов"],
                "cta_style": cta_style,
                "top_words": top_words,
                "avg_views": avg_views,
                "max_views": max_views,
                "min_views": min_views,
                "avg_reactions": avg_reactions,
                "total_reactions": total_reactions,
                "avg_forwards": avg_forwards,
                "engagement_rate": engagement,
                # Новые поля для автоподстройки
                "recommended_temperature": recommended_temperature,
                "content_type": content_type,
            },
            "examples": {
                "hooks": first_lines[:3],
                "endings": last_lines[:3],
            }
        }

    registry.register(
        name="compute_channel_metrics",
        handler=compute_channel_metrics,
        description="Вычисление метрик канала без LLM",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_analyze"],
        parameters={
            "posts": {"type": "array", "required": True},
        }
    )

    # ==================== EDIT TOOLS ====================

    def parse_edit_intent(edit_request: str, original_text: str) -> Dict[str, Any]:
        """
        Парсинг интента редактирования — КОД, не LLM.

        Возвращает список операций:
        - add_hook: добавить хук в начало
        - delete_last_paragraph: удалить последний абзац
        - delete_first_paragraph: удалить первый абзац
        - delete_hashtags: удалить хэштеги
        - add_paragraph: добавить абзац (нужна генерация)
        - bold_text: выделить жирным
        - unbold_text: убрать жирный
        """
        import re

        request_lower = edit_request.lower()
        operations = []

        # Хук / зацепка / крючок → добавить в начало
        if any(w in request_lower for w in ['хук', 'зацепк', 'крючок', 'цепляющ']):
            operations.append({
                "type": "add_hook",
                "position": "start",
                "needs_generation": True,
            })

        # Удалить последний абзац
        if ('последн' in request_lower and 'абзац' in request_lower) or \
           ('убери' in request_lower and 'конец' in request_lower):
            # Находим последний абзац
            paragraphs = [p for p in original_text.split('\n\n') if p.strip()]
            if paragraphs:
                operations.append({
                    "type": "delete_paragraph",
                    "position": "last",
                    "content": paragraphs[-1],
                })

        # Удалить первый абзац
        if 'перв' in request_lower and 'абзац' in request_lower and 'убер' in request_lower:
            paragraphs = [p for p in original_text.split('\n\n') if p.strip()]
            if paragraphs:
                operations.append({
                    "type": "delete_paragraph",
                    "position": "first",
                    "content": paragraphs[0],
                })

        # Хэштеги
        if any(w in request_lower for w in ['хэштег', 'хештег', 'hashtag', '#']):
            hashtags = re.findall(r'#\w+', original_text)
            if hashtags:
                if 'убер' in request_lower or 'удал' in request_lower:
                    operations.append({
                        "type": "delete_hashtags",
                        "hashtags": hashtags,
                    })
                elif 'добав' in request_lower:
                    operations.append({
                        "type": "add_hashtags",
                        "needs_generation": True,
                    })

        # Добавить абзац
        if 'добав' in request_lower and 'абзац' in request_lower:
            # Определяем позицию
            position = "end"
            if 'начал' in request_lower or 'перв' in request_lower:
                position = "start"
            elif 'середин' in request_lower or 'между' in request_lower:
                position = "middle"

            operations.append({
                "type": "add_paragraph",
                "position": position,
                "needs_generation": True,
                "context": edit_request,  # что именно добавить
            })

        # Жирный текст
        bold_match = re.search(r'(выдел|сделай).*(жирн|чёрн|черн)', request_lower)
        if bold_match:
            # Ищем что выделить
            operations.append({
                "type": "bold_text",
                "context": edit_request,
            })

        # Убрать жирный
        unbold_match = re.search(r'(убер|удал).*(жирн|чёрн|черн)', request_lower)
        if unbold_match:
            operations.append({
                "type": "unbold_text",
                "context": edit_request,
            })

        # Сделать короче
        if 'корот' in request_lower or 'сократ' in request_lower:
            operations.append({
                "type": "shorten",
                "needs_generation": True,
            })

        # Сделать длиннее / больше текста
        if 'длинн' in request_lower or 'больше текст' in request_lower or 'разверн' in request_lower:
            operations.append({
                "type": "expand",
                "needs_generation": True,
            })

        return {
            "operations": operations,
            "needs_llm": any(op.get("needs_generation") for op in operations),
            "original_length": len(original_text),
        }

    registry.register(
        name="parse_edit_intent",
        handler=parse_edit_intent,
        description="Парсинг интента редактирования (код)",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_edit"],
        parameters={
            "edit_request": {"type": "string", "required": True},
            "original_text": {"type": "string", "required": True},
        }
    )

    def apply_edit_operations(
        original_text: str,
        operations: List[Dict],
        generated_content: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Применение операций редактирования — КОД, не LLM.

        Точечные изменения без переписывания всего текста.
        """
        import re

        result = original_text
        applied = []

        generated_content = generated_content or {}

        for op in operations:
            op_type = op.get("type", "")

            if op_type == "add_hook":
                hook = generated_content.get("hook", "")
                if hook:
                    result = hook.strip() + "\n\n" + result
                    applied.append(f"добавлен хук: {hook[:30]}...")

            elif op_type == "delete_paragraph":
                content = op.get("content", "")
                if content and content in result:
                    result = result.replace(content, "").strip()
                    # Убираем лишние переносы
                    result = re.sub(r'\n{3,}', '\n\n', result)
                    applied.append(f"удалён абзац: {content[:30]}...")

            elif op_type == "delete_hashtags":
                hashtags = op.get("hashtags", [])
                for tag in hashtags:
                    result = result.replace(tag, "")
                result = re.sub(r'\n{3,}', '\n\n', result).strip()
                applied.append(f"удалены хэштеги: {len(hashtags)} шт")

            elif op_type == "add_hashtags":
                hashtags = generated_content.get("hashtags", "")
                if hashtags:
                    result = result.strip() + "\n\n" + hashtags
                    applied.append(f"добавлены хэштеги")

            elif op_type == "add_paragraph":
                paragraph = generated_content.get("paragraph", "")
                position = op.get("position", "end")
                if paragraph:
                    if position == "start":
                        # После первого абзаца
                        parts = result.split('\n\n', 1)
                        if len(parts) > 1:
                            result = parts[0] + "\n\n" + paragraph + "\n\n" + parts[1]
                        else:
                            result = result + "\n\n" + paragraph
                    elif position == "middle":
                        parts = result.split('\n\n')
                        mid = len(parts) // 2
                        parts.insert(mid, paragraph)
                        result = '\n\n'.join(parts)
                    else:  # end
                        result = result.strip() + "\n\n" + paragraph
                    applied.append(f"добавлен абзац: {paragraph[:30]}...")

            elif op_type == "bold_text":
                target = op.get("target", "")
                if target and target in result and f"<b>{target}</b>" not in result:
                    result = result.replace(target, f"<b>{target}</b>", 1)
                    applied.append(f"выделено жирным: {target[:20]}")

            elif op_type == "unbold_text":
                # Убираем все <b></b> теги
                result = re.sub(r'<b>(.*?)</b>', r'\1', result)
                applied.append("убран жирный текст")

        return {
            "result": result.strip(),
            "applied": applied,
            "operations_count": len(applied),
        }

    registry.register(
        name="apply_edit_operations",
        handler=apply_edit_operations,
        description="Применение операций редактирования (код)",
        impact=ToolImpact.LOW,
        allowed_task_types=["smm", "smm_edit"],
        parameters={
            "original_text": {"type": "string", "required": True},
            "operations": {"type": "array", "required": True},
            "generated_content": {"type": "object", "required": False},
        }
    )

    print(f"[Tools] Зарегистрировано SMM tools: {registry.list_names()}")
