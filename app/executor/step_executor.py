"""
Yadro v0 - Step Executor

Executes individual steps in the plan.
"""
from typing import Any, Optional, Callable, Dict
from datetime import datetime, timezone

from .models import Step, StepAction, StepStatus, ExecutionContext
from ..kernel import TaskManager, PauseReason
from ..tools.registry import registry as tool_registry


class ApprovalRequired(Exception):
    """Raised when step requires user approval."""

    def __init__(self, message: str, step_id: str, draft_content: Optional[str] = None):
        super().__init__(message)
        self.step_id = step_id
        self.draft_content = draft_content


def _markdown_to_html(text: str) -> str:
    """Конвертация markdown в HTML для Telegram. Архитектурное решение."""
    import re

    # Убираем markdown заголовки (### Идея → Идея)
    # LLM часто отвечает с ### для структуры — убираем решётки
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)

    # Порядок важен! Сначала двойные, потом одинарные
    # __bold__ → <b>bold</b>
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)
    # **bold** → <b>bold</b> (разрешаем многострочный контент)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    # _italic_ → <i>italic</i> (но не внутри слов типа snake_case)
    text = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)', r'<i>\1</i>', text)
    # *italic* → <i>italic</i>
    text = re.sub(r'(?<!\w)\*([^\*\n]+?)\*(?!\w)', r'<i>\1</i>', text)

    # Очистка вложенных/дублированных тегов
    # <b><b>text</b></b> → <b>text</b>
    while '<b><b>' in text:
        text = text.replace('<b><b>', '<b>')
    while '</b></b>' in text:
        text = text.replace('</b></b>', '</b>')
    while '<i><i>' in text:
        text = text.replace('<i><i>', '<i>')
    while '</i></i>' in text:
        text = text.replace('</i></i>', '</i>')

    return text


def _apply_style_postprocess(text: str, smm_context: str) -> str:
    """
    Пост-процессор: применяет стиль канала к посту (архитектурно).

    Логика:
    1. Есть "ТВОЙ СТИЛЬ" → берём эмодзи/форматирование оттуда
    2. Нет стиля → применяем дефолтное форматирование (жирный заголовок)
    3. Только "ИНСАЙТЫ конкурентов" → не копируем их стиль, но дефолт применяем
    """
    import re

    context_lower = smm_context.lower() if smm_context else ""

    # Определяем что есть в контексте
    has_own_style = 'твой стиль' in context_lower or 'примеры постов' in context_lower
    has_insights_only = ('инсайты' in context_lower or 'не копируй' in context_lower) and not has_own_style

    emoji_pattern = re.compile("[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\u2300-\u23FF]+")
    has_emoji = bool(emoji_pattern.search(text))
    has_bold = '<b>' in text

    # === 1. ЖИРНЫЙ ЗАГОЛОВОК ===
    # Добавляем всегда если нет (дефолтное форматирование)
    if not has_bold:
        # Ищем первое предложение или первую строку
        first_line = text.split('\n')[0]
        # Если первая строка короткая (до 100 символов) — это заголовок
        if len(first_line) < 100 and first_line.strip():
            text = f"<b>{first_line}</b>" + text[len(first_line):]
            print(f"[PostProcess] Добавлен жирный заголовок (дефолт)")
        else:
            # Иначе берём первое предложение
            sentences = re.split(r'(?<=[.!?])\s+', text, maxsplit=1)
            if sentences and len(sentences[0]) < 150:
                text = f"<b>{sentences[0]}</b>"
                if len(sentences) > 1:
                    text += "\n\n" + sentences[1]
                print(f"[PostProcess] Добавлен жирный заголовок (первое предложение)")

    # === 2. ЭМОДЗИ ===
    anti_emoji = ['без эмодзи', 'мало эмодзи', 'редко эмодзи', '0 на пост', 'не использует эмодзи']
    emoji_forbidden = any(k in context_lower for k in anti_emoji)

    if not has_emoji and not emoji_forbidden:
        if has_own_style:
            # Берём эмодзи из собственного стиля
            own_style_match = re.search(r'(ТВОЙ СТИЛЬ|ПРИМЕРЫ ПОСТОВ)[^\n]*\n([\s\S]*?)(?=\n\n[А-Я]|$)', smm_context)
            style_section = own_style_match.group(2) if own_style_match else smm_context
            context_emojis = emoji_pattern.findall(style_section)

            if context_emojis:
                emoji_set = list(set(context_emojis))[:3]
                text = f"{emoji_set[0]} " + text
                print(f"[PostProcess] Добавлен эмодзи из стиля: {emoji_set[0]}")
        else:
            # Дефолтный эмодзи в начало — нейтральный
            # Выбираем по теме из текста
            text_lower = text.lower()
            if any(w in text_lower for w in ['деньг', 'финанс', 'банк', 'инвест', 'рубл', 'доллар']):
                default_emoji = '💰'
            elif any(w in text_lower for w in ['технолог', 'ai', 'ии', 'робот', 'код', 'приложен']):
                default_emoji = '🤖'
            elif any(w in text_lower for w in ['бизнес', 'стартап', 'компан', 'рынок', 'продаж']):
                default_emoji = '📈'
            elif any(w in text_lower for w in ['здоров', 'спорт', 'еда', 'питан', 'фитнес']):
                default_emoji = '💪'
            else:
                default_emoji = '📌'

            text = f"{default_emoji} " + text
            print(f"[PostProcess] Добавлен дефолтный эмодзи: {default_emoji}")

    return text


class StepExecutor:
    """
    Executes individual steps.

    Routes step actions to appropriate handlers:
    - LLM_CALL → LLM Service
    - TOOL_CALL → Tool Runtime
    - APPROVAL → Pause for user
    - CONDITION → Evaluate and decide
    - AGGREGATE → Combine results
    """

    def __init__(self, task_manager: Optional[TaskManager] = None, llm_service=None):
        """
        Initialize StepExecutor.

        Args:
            task_manager: TaskManager for pausing tasks
            llm_service: LLMService for LLM calls
        """
        self._task_manager = task_manager
        self._llm_service = llm_service

        # Handler registry
        self._handlers: Dict[StepAction, Callable] = {
            StepAction.LLM_CALL: self._handle_llm_call,
            StepAction.TOOL_CALL: self._handle_tool_call,
            StepAction.APPROVAL: self._handle_approval,
            StepAction.CONDITION: self._handle_condition,
            StepAction.AGGREGATE: self._handle_aggregate,
        }

    @property
    def task_manager(self) -> TaskManager:
        """Get task manager (lazy init)."""
        if self._task_manager is None:
            self._task_manager = TaskManager()
        return self._task_manager

    def execute(self, step: Step, context: ExecutionContext) -> Any:
        """
        Execute a single step.

        Args:
            step: Step to execute
            context: Execution context

        Returns:
            Step result

        Raises:
            ApprovalRequired: If step needs user approval
            Exception: If step execution fails
        """
        handler = self._handlers.get(step.action)
        if handler is None:
            raise ValueError(f"Unknown step action: {step.action}")

        # Mark step as running
        step.status = StepStatus.RUNNING
        step.started_at = datetime.now(timezone.utc)

        try:
            result = handler(step, context)

            # Mark success
            step.status = StepStatus.COMPLETED
            step.result = result
            step.completed_at = datetime.now(timezone.utc)

            # Store in context
            context.add_step_result(step.step_id, result)
            context.steps_executed += 1

            return result

        except ApprovalRequired:
            # Step paused for approval - reset to pending
            step.status = StepStatus.PENDING
            step.started_at = None
            raise

        except Exception as e:
            step.status = StepStatus.FAILED
            step.error = str(e)
            step.completed_at = datetime.now(timezone.utc)
            raise

    # ==================== HANDLERS ====================

    def _handle_llm_call(self, step: Step, context: ExecutionContext) -> Any:
        """
        Handle LLM call step.

        Использует реальный LLM Service и SMM-специфичные промпты.
        """
        purpose = step.action_data.get("purpose", "general")
        input_text = step.action_data.get("input_text") or context.input_text
        system_prompt = step.action_data.get("system_prompt", "")
        prompt_template = step.action_data.get("prompt", "")
        smm_context = step.action_data.get("smm_context", "")

        # Собираем контекст из предыдущих шагов
        prev_results = []
        for dep_id in step.depends_on:
            dep_result = context.get_step_result(dep_id)
            if dep_result:
                prev_results.append(dep_result)

        # Если есть реальный LLM — используем его
        if self._llm_service is not None:
            try:
                from ..llm import Message

                # Получаем системный промпт и user prompt для SMM
                if purpose.startswith("smm_"):
                    sys_prompt, user_prompt = self._build_smm_prompt(
                        purpose, input_text, prev_results, smm_context, step.action_data
                    )
                elif prompt_template:
                    sys_prompt = system_prompt or self._get_system_prompt(purpose)
                    user_prompt = prompt_template.format(
                        input=input_text or "",
                        context=prev_results,
                        **step.action_data
                    )
                else:
                    sys_prompt = system_prompt or self._get_system_prompt(purpose)
                    user_prompt = self._build_prompt(purpose, input_text, prev_results, step.action_data)

                print(f"[Step] LLM_CALL: {purpose}")

                # Для генерации постов используем Claude
                model = None
                temperature = 0.7  # default
                if purpose.startswith("smm_generate"):
                    model = "claude-sonnet-4"  # Claude лучше пишет и следует инструкциям

                    # Температура: берём из анализа каналов или определяем по контексту
                    recommended_temp = step.action_data.get("recommended_temperature")
                    if recommended_temp:
                        temperature = float(recommended_temp)
                        print(f"[Step] Температура из анализа каналов: {temperature}")
                    else:
                        # Fallback: по наличию данных
                        has_context = bool(smm_context) or any(
                            isinstance(r, dict) and r.get("tool") == "web_search" and r.get("results")
                            for r in prev_results
                        )
                        temperature = 0.3 if has_context else 0.6
                        print(f"[Step] Температура по контексту: {temperature}")

                response = self._llm_service.complete(
                    messages=[
                        Message.system(sys_prompt),
                        Message.user(user_prompt)
                    ],
                    model=model,
                    temperature=temperature,
                    user_id=context.user_id,
                    task_id=context.task_id
                )

                print(f"[Step] LLM_CALL: {purpose} → OK ({response.total_tokens} tokens)")

                # Постобработка для SMM постов (архитектурно)
                content = response.content
                if purpose.startswith("smm_generate"):
                    content = _markdown_to_html(content)
                    content = _apply_style_postprocess(content, smm_context)

                return {
                    "purpose": purpose,
                    "response": content,
                    "model": response.model,
                    "tokens_used": response.total_tokens,
                }

            except Exception as e:
                print(f"[Step] LLM_CALL: {purpose} → ERROR: {e}")
                return {"purpose": purpose, "error": str(e)}

        # Fallback: mock
        print(f"[Step] LLM_CALL: {purpose} → MOCK (no llm_service)")
        mock_responses = {
            "analyze": f"Analysis of: {input_text[:50] if input_text else 'N/A'}...",
            "research": f"Research findings for: {input_text[:50] if input_text else 'N/A'}",
            "smm_generate_post": f"Пост про {input_text}:\n\nЭто тестовый пост. #тест",
            "smm_analyze_style": "Стиль: дерзкий, короткие посты, много эмодзи",
            "summarize": f"Summary: {input_text[:100] if input_text else 'N/A'}...",
        }

        return {
            "purpose": purpose,
            "response": mock_responses.get(purpose, f"Mock response for {purpose}"),
            "model": "mock",
            "tokens_used": 0,
        }

    def _build_smm_prompt(
        self,
        purpose: str,
        input_text: str,
        prev_results: list,
        smm_context: str,
        action_data: dict
    ) -> tuple:
        """Построение промптов для SMM задач. Возвращает (system_prompt, user_prompt)."""

        if purpose == "smm_generate_post":
            system_prompt = """Ты копирайтер для Telegram. Сейчас 2026 год.

КАК ПИСАТЬ ХОРОШО:
1. Начни с интересного факта или провокации — зацепи читателя
2. Дай контекст — почему это важно сейчас
3. Добавь нюанс или противоречие — покажи что тема сложнее чем кажется
4. Закончи выводом или вопросом

ОТКУДА БРАТЬ ФАКТЫ:
- Есть "АКТУАЛЬНАЯ ИНФА"? Бери цифры, названия компаний, исследования ОТТУДА
- Нет данных? Пиши без конкретных цифр, рассуждай
- НИКОГДА не выдумывай статистику, проценты, "исследования показали"

СТИЛЬ:
- Есть "ТВОЙ СТИЛЬ" или "ПРИМЕРЫ"? Копируй их формат и тон
- Нет примеров? Пиши как журналист — от 3-го лица, без "я проверил"
- Связный текст, НЕ нумерованные списки
- 3-5 коротких абзацев
- 1-2 эмодзи максимум, и только если к месту

ЗАПРЕЩЕНО:
❌ Списки "5 способов", "7 правил", "10 лайфхаков"
❌ Выдуманные цифры ("на 30% эффективнее", "500 мл воды")
❌ "Проверил на себе", "работает!" — фейковый опыт
❌ "в современном мире", "не просто X, а Y"
❌ Эмодзи в каждом абзаце"""

            # Собираем инфо из предыдущих шагов
            similar_posts = ""
            web_info = ""

            for res in prev_results:
                if isinstance(res, dict):
                    if res.get("tool") == "memory_search":
                        results = res.get("results", [])
                        if results:
                            similar_posts = "\n".join([
                                f"• {r.get('content', '')[:200]}"
                                for r in results[:3]
                            ])
                    elif res.get("tool") == "web_search":
                        results = res.get("results", [])
                        if results:
                            web_info = "\n".join([
                                f"• {r.get('title', '')}: {r.get('summary', '')[:150]}"
                                for r in results[:3]
                            ])

            # Собираем user prompt
            parts = []

            if smm_context:
                parts.append(smm_context)

            if similar_posts:
                parts.append(f"ПОХОЖИЕ ПОСТЫ (вдохновляйся):\n{similar_posts}")

            if web_info:
                parts.append(f"АКТУАЛЬНАЯ ИНФА ИЗ ИНТЕРНЕТА (используй факты!):\n{web_info}")

            context_text = "\n\n".join(parts)

            # Формируем user prompt
            has_web_data = bool(web_info)

            user_prompt = f"""Напиши пост для Telegram.

{context_text}

ТЕМА: {input_text}

{"ЕСТЬ ДАННЫЕ ИЗ ИНТЕРНЕТА — используй факты оттуда, не выдумывай свои!" if has_web_data else "НЕТ ДАННЫХ ИЗ ИНТЕРНЕТА — пиши без конкретных цифр и статистики, рассуждай общими словами."}

Формат: 3-5 абзацев, связный текст (не список), 1-2 эмодзи если к месту."""

            return system_prompt, user_prompt

        elif purpose == "smm_analyze_style":
            system_prompt = "Ты копирайтер-аналитик. Разбираешь стиль постов для копирайтера."

            # Получаем посты из parse_channel
            posts_text = ""
            channel = input_text

            for res in prev_results:
                if isinstance(res, dict) and res.get("tool") == "parse_channel":
                    posts = res.get("posts", [])
                    if posts:
                        # Фильтруем рекламу
                        organic = [p for p in posts if not self._is_ad_post(p.get("text", ""))][:5]
                        posts_text = "\n".join([
                            f"[{p.get('views', 0)} views] {p.get('text', '')[:200]}"
                            for p in organic
                        ])

            user_prompt = f"""Разбери стиль канала {channel} для копирайтера.

ПОСТЫ:
{posts_text}

Дай КОНКРЕТИКУ:
1. ЛИЦО ПОВЕСТВОВАНИЯ — от какого лица пишут?
   - 1-е лицо ("я", "мы", "мне")
   - 3-е лицо/безличный ("компания", "было решено")
   Это КРИТИЧЕСКИ важно для копирования стиля!
2. HOOKS — как цепляют внимание? Примеры фраз.
3. СТРУКТУРА — как строят пост?
4. ФИШКИ — характерные приёмы, слова, выражения.
5. КОНЦОВКА — CTA? Вопрос? Ничего?
6. ДЛИНА — короткие/средние/длинные посты?

Конкретно, с примерами. Начни с ЛИЦА — это главное."""

            return system_prompt, user_prompt

        elif purpose == "smm_deep_analyze":
            # ГЛУБОКИЙ АНАЛИЗ — получаем готовые метрики из предыдущих шагов
            system_prompt = "Ты копирайтер-аналитик. Делаешь глубокий разбор стиля канала для копирайтера. Сейчас 2026 год."

            channel = input_text
            posts_data = None
            metrics_data = None

            for res in prev_results:
                if isinstance(res, dict):
                    if res.get("tool") == "parse_channel":
                        posts_data = res
                    elif res.get("tool") == "compute_channel_metrics":
                        metrics_data = res.get("metrics", {})

            # Формируем контекст из метрик
            metrics_text = ""
            if metrics_data:
                metrics_text = f"""
МЕТРИКИ (вычислены автоматически):
- Длина постов: {metrics_data.get('length_category', '?')} (в среднем {metrics_data.get('avg_length', 0)} символов)
- Эмодзи: {metrics_data.get('emoji_style', '?')} ({metrics_data.get('avg_emoji', 0)} на пост)
- Хештеги: {metrics_data.get('avg_hashtags', 0)} на пост, топ: {', '.join(metrics_data.get('top_hashtags', [])[:3])}
- Структура: {', '.join(metrics_data.get('structure', []))}
- Хуки: {', '.join(metrics_data.get('hook_patterns', []))}
- Концовки: {metrics_data.get('cta_style', '?')}
- Топ слова: {', '.join(metrics_data.get('top_words', [])[:7])}
- Просмотры: ~{metrics_data.get('avg_views', 0)}"""

            # Примеры хуков и концовок
            examples = metrics_data.get("examples", {}) if isinstance(metrics_data, dict) else {}
            examples_text = ""
            if examples:
                hooks = examples.get("hooks", [])
                endings = examples.get("endings", [])
                if hooks:
                    examples_text += f"\n\nПРИМЕРЫ ХУКОВ:\n" + "\n".join([f"• {h[:60]}..." for h in hooks[:3]])
                if endings:
                    examples_text += f"\n\nПРИМЕРЫ КОНЦОВОК:\n" + "\n".join([f"• {e[:60]}..." for e in endings[:3]])

            # Несколько постов для контекста
            posts_text = ""
            if posts_data and posts_data.get("posts"):
                posts = posts_data.get("posts", [])[:3]
                posts_text = "\n\nЛУЧШИЕ ПОСТЫ:\n" + "\n---\n".join([
                    f"[{p.get('views', 0)} views] {p.get('text', '')[:300]}..."
                    for p in posts
                ])

            user_prompt = f"""Проанализируй канал {channel} для копирайтера.
{metrics_text}
{examples_text}
{posts_text}

ЗАДАЧА: На основе МЕТРИК и ПРИМЕРОВ выдели:

1. ЛИЦО ПОВЕСТВОВАНИЯ — КРИТИЧЕСКИ ВАЖНО!
   - 1-е лицо ("я думаю", "мы сделали", "мне кажется")
   - 3-е лицо/безличный ("компания объявила", "было решено", "эксперты считают")
   Определи точно — от этого зависит весь стиль!
2. TONE OF VOICE — как бренд разговаривает? (формальный/дерзкий/дружеский)
3. ФОРМУЛА ПОСТА — типичная структура: hook → body → CTA
4. ФИРМЕННЫЕ ПРИЁМЫ — что отличает этот канал?
5. ТРИГГЕРЫ ВОВЛЕЧЕНИЯ — почему читают и реагируют?
6. РЕКОМЕНДАЦИИ — как писать в этом стиле?

Начни с ЛИЦА ПОВЕСТВОВАНИЯ — это определяет всё остальное.
Конкретно, с примерами фраз."""

            return system_prompt, user_prompt

        elif purpose == "smm_generate_edit_content":
            # ГЕНЕРАЦИЯ КОНТЕНТА ДЛЯ РЕДАКТИРОВАНИЯ
            # LLM генерит ТОЛЬКО нужный контент (хук, абзац), НЕ видит весь пост
            system_prompt = """Ты SMM-копирайтер. Генерируешь ТОЛЬКО запрошенный контент.

НЕ пиши весь пост — только то что просят: хук, абзац, хэштеги.
Учитывай стиль из контекста."""

            topic = action_data.get("topic", "")

            # Собираем контекст из предыдущих шагов
            style_context = ""
            web_context = ""
            operations = []

            for res in prev_results:
                if isinstance(res, dict):
                    # Интент редактирования
                    if res.get("tool") == "parse_edit_intent":
                        operations = res.get("operations", [])
                    # Контекст из памяти
                    elif res.get("tool") == "memory_search":
                        results = res.get("results", [])
                        if results:
                            style_context = "\n".join([r.get("content", "")[:200] for r in results[:3]])
                    # Web search
                    elif res.get("tool") == "web_search":
                        results = res.get("results", [])
                        if results:
                            web_context = "\n".join([f"• {r.get('title', '')}: {r.get('snippet', '')[:100]}" for r in results[:3]])

            # Определяем что нужно сгенерировать
            needs = []
            for op in operations:
                if op.get("needs_generation"):
                    op_type = op.get("type", "")
                    if op_type == "add_hook":
                        needs.append(f"HOOK: цепляющее первое предложение про '{topic}'. 1-2 предложения, с эмодзи.")
                    elif op_type == "add_paragraph":
                        context = op.get("context", "")
                        needs.append(f"PARAGRAPH: абзац на тему '{context}'. 2-3 предложения.")
                    elif op_type == "add_hashtags":
                        needs.append(f"HASHTAGS: 3-5 релевантных хэштегов для поста про '{topic}'")
                    elif op_type == "shorten":
                        needs.append("SHORTEN: укажи какие части можно сократить")
                    elif op_type == "expand":
                        needs.append(f"EXPAND: дополнительный контент про '{topic}'. 2-3 предложения.")

            if not needs:
                return system_prompt, "Ничего не нужно генерировать."

            user_prompt = f"""ТЕМА ПОСТА: {topic}

СТИЛЬ (из успешных постов):
{style_context if style_context else 'Нет данных'}

АКТУАЛЬНАЯ ИНФОРМАЦИЯ:
{web_context if web_context else 'Нет данных'}

НУЖНО СГЕНЕРИРОВАТЬ:
{chr(10).join(needs)}

Ответь в формате JSON:
{{
  "hook": "текст хука если нужен",
  "paragraph": "текст абзаца если нужен",
  "hashtags": "#тег1 #тег2 если нужны"
}}

ТОЛЬКО JSON, без комментариев."""

            return system_prompt, user_prompt

        # Default
        return "Ты полезный ассистент.", input_text or str(action_data)

    def _is_ad_post(self, text: str) -> bool:
        """Проверка на рекламный пост."""
        ad_markers = [
            '#реклама', '#ad', '#промо', '#promo', 'реклама',
            'переходи по ссылке', 'купить', 'скидка', 'промокод',
            'закажи', 'оплати', 'подписывайся', 'регистрируйся'
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in ad_markers)

    def _get_system_prompt(self, purpose: str) -> str:
        """Системный промпт по типу задачи."""
        prompts = {
            "analyze_style": "Ты аналитик контента. Разбираешь стиль постов для копирайтера.",
            "generate_draft": "Ты SMM-копирайтер. Пишешь посты для Telegram. Выделяй важное жирным.",
            "research": "Ты исследователь. Анализируешь информацию и выделяешь ключевое.",
            "analyze": "Ты аналитик. Разбираешь данные и делаешь выводы.",
        }
        return prompts.get(purpose, "Ты полезный ассистент.")

    def _build_prompt(self, purpose: str, input_text: str, prev_results: list, action_data: dict) -> str:
        """Построение промпта по типу задачи."""
        if purpose == "analyze_style":
            posts = action_data.get("posts", prev_results[0] if prev_results else "")
            return f"""Разбери стиль постов для копирайтера.

ПОСТЫ:
{posts}

Дай КОНКРЕТИКУ:
1. HOOKS — как цепляют внимание? Примеры фраз.
2. СТРУКТУРА — как строят пост?
3. ФИШКИ — характерные приёмы, слова.
4. КОНЦОВКА — как заканчивают?
5. ДЛИНА — сколько слов?

Без воды типа "дружеский тон"."""

        elif purpose == "generate_draft":
            context = action_data.get("context", "")
            topic = input_text or action_data.get("topic", "")
            web_info = ""
            similar = ""

            for res in prev_results:
                if isinstance(res, dict):
                    if res.get("tool") == "web_search":
                        web_info = str(res.get("results", ""))[:1000]
                    elif res.get("tool") == "memory_search":
                        similar = str(res.get("results", ""))[:500]

            return f"""Напиши пост для Telegram-канала.

{context}

ТЕМА: {topic}

{"ПОХОЖИЕ ПОСТЫ (вдохновляйся):" + similar if similar else ""}

{"АКТУАЛЬНАЯ ИНФА:" + web_info if web_info else ""}

ОБЯЗАТЕЛЬНО:
1. Следуй стилю клиента
2. Выделяй важное жирным
3. Напиши только текст поста"""

        elif purpose == "research":
            return f"Исследуй тему: {input_text}\n\nПредыдущие результаты: {prev_results}"

        else:
            return input_text or str(action_data)

    def _handle_tool_call(self, step: Step, context: ExecutionContext) -> Any:
        """
        Handle tool call step.

        Использует ToolRegistry для вызова реальных tools.
        """
        tool_name = step.action_data.get("tool", "unknown")
        params = {k: v for k, v in step.action_data.items() if k not in ("tool", "source_step_id")}

        # Добавляем user_id из контекста если нужен
        if "user_id" not in params:
            params["user_id"] = context.user_id

        # Особый случай: source_step_id — берём данные из предыдущего шага
        source_step_id = step.action_data.get("source_step_id")

        # compute_channel_metrics — нужны posts из parse_channel
        if tool_name == "compute_channel_metrics" and source_step_id:
            source_result = context.get_step_result(source_step_id)
            if source_result and isinstance(source_result, dict):
                posts = source_result.get("posts", [])
                params["posts"] = posts

        # memory_store — берём response из LLM + temperature из metrics
        if tool_name == "memory_store" and source_step_id:
            source_result = context.get_step_result(source_step_id)
            if source_result and isinstance(source_result, dict):
                response_content = source_result.get("response", "")
                channel = step.action_data.get("input_text", "channel")
                params["content"] = f"Стиль канала {channel}: {response_content[:1500]}"

                # Ищем temperature из compute_channel_metrics в результатах
                recommended_temp = 0.5  # default
                content_type = "экспертный"
                for step_id, result in context.step_results.items():
                    if isinstance(result, dict) and result.get("tool") == "compute_channel_metrics":
                        metrics = result.get("metrics", {})
                        recommended_temp = metrics.get("recommended_temperature", 0.5)
                        content_type = metrics.get("content_type", "экспертный")
                        break

                # Сохраняем temperature в metadata
                params["metadata"] = {
                    "analysis_version": "v2",
                    "channel": channel,
                    "recommended_temperature": recommended_temp,
                    "content_type": content_type,
                }
                print(f"[Step] memory_store: {channel} → temp={recommended_temp}, type={content_type}")

        # Пробуем вызвать реальный tool
        tool_spec = tool_registry.get(tool_name)
        if tool_spec is not None:
            try:
                # Фильтруем параметры — оставляем только те, что tool принимает
                import inspect
                sig = inspect.signature(tool_spec.handler)
                valid_params = set(sig.parameters.keys())
                filtered_params = {k: v for k, v in params.items() if k in valid_params}

                print(f"[Step] TOOL_CALL: {tool_name} с {list(filtered_params.keys())}")
                result = tool_spec.handler(**filtered_params)
                print(f"[Step] TOOL_CALL: {tool_name} → OK")
                return {"tool": tool_name, **result}
            except Exception as e:
                print(f"[Step] TOOL_CALL: {tool_name} → ERROR: {e}")
                return {"tool": tool_name, "error": str(e)}

        # Fallback: mock responses для незарегистрированных tools
        print(f"[Step] TOOL_CALL: {tool_name} → MOCK (not registered)")
        mock_responses = {
            "web_fetch": {
                "tool": "web_fetch",
                "content": "Fetched page content...",
                "url": step.action_data.get("url"),
            },
            "telegram_publish": {
                "tool": "telegram_publish",
                "success": True,
                "message_id": 12345,
                "channel": step.action_data.get("channel"),
            },
        }

        return mock_responses.get(tool_name, {"tool": tool_name, "result": "mock"})

    def _handle_approval(self, step: Step, context: ExecutionContext) -> Any:
        """
        Handle approval step.

        Pauses task execution and waits for user approval.
        """
        message = step.action_data.get("message", "Approval required")

        # Get draft content if referenced
        draft_step_id = step.action_data.get("draft_step_id")
        draft_content = None
        if draft_step_id:
            draft_result = context.get_step_result(draft_step_id)
            print(f"[Step] APPROVAL: draft_step_id={draft_step_id}, draft_result keys={list(draft_result.keys()) if draft_result else 'None'}")
            if draft_result:
                if "error" in draft_result:
                    print(f"[Step] APPROVAL: LLM error: {draft_result.get('error')}")
                    # Показываем ошибку вместо пустого поста
                    draft_content = f"Ошибка генерации: {draft_result.get('error')}"
                else:
                    draft_content = draft_result.get("response")

        # Pause task for approval
        self.task_manager.pause(
            context.task_id,
            PauseReason.APPROVAL,
            data={
                "step_id": step.step_id,
                "message": message,
                "draft_content": draft_content,
            }
        )

        # Raise to stop execution
        raise ApprovalRequired(message, step.step_id, draft_content)

    def _handle_condition(self, step: Step, context: ExecutionContext) -> Any:
        """Handle conditional step."""
        condition = step.action_data.get("condition", "true")

        # MVP: Simple evaluation
        result = True

        return {
            "condition": condition,
            "result": result,
            "branch": "true" if result else "false",
        }

    def _handle_aggregate(self, step: Step, context: ExecutionContext) -> Any:
        """Handle aggregation step."""
        step_ids = step.action_data.get("step_ids", [])

        aggregated = {}
        for step_id in step_ids:
            result = context.get_step_result(step_id)
            if result:
                aggregated[step_id] = result

        return {
            "aggregated": aggregated,
            "count": len(aggregated),
        }
