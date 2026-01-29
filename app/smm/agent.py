"""
SMM Agent - архитектурная версия

Использует Executor → Plan → Steps для всех операций:
- Layer 2: Kernel (TaskManager)
- Layer 3: Executor (Plan, Step)
- Layer 4: Tools (ToolRegistry)
- Layer 5: LLM
- Layer 6: Memory (FTS5)
"""
import re
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timedelta

from app.storage import Database
from app.kernel import TaskManager, PauseReason
from app.kernel.models import TaskStatus
from app.scheduler import Scheduler
from app.memory import MemoryService, MemoryType
from app.llm import LLMService, Message
from app.executor import Executor, PlanManager, StepExecutor
from app.executor.step_executor import ApprovalRequired, _markdown_to_html
from app.tools.channel_parser import ChannelParser
from app.tools.news_monitor import NewsMonitor


@dataclass
class PostDraft:
    text: str
    topic: str
    task_id: int = 0
    channel_id: str = ""


class SMMAgent:
    """
    SMM Agent — архитектурная версия.

    Все операции выполняются через Executor → Plan → Steps.
    """

    def __init__(self, db: Database, llm: LLMService):
        self.db = db
        self.llm = llm
        self.tasks = TaskManager(db=db)
        self.scheduler = Scheduler(db=db)
        self.memory = MemoryService(db=db)
        self._parser = None
        self._news = None
        self._executor = None

    @property
    def executor(self) -> Executor:
        """Executor для выполнения задач через Plan/Step."""
        if self._executor is None:
            step_executor = StepExecutor(
                task_manager=self.tasks,
                llm_service=self.llm
            )
            self._executor = Executor(
                db=self.db,
                task_manager=self.tasks,
                step_executor=step_executor,
            )
        return self._executor

    @property
    def parser(self) -> ChannelParser:
        if self._parser is None:
            self._parser = ChannelParser()
        return self._parser

    @property
    def news(self) -> NewsMonitor:
        if self._news is None:
            self._news = NewsMonitor()
        return self._news

    # ==================== ПАМЯТЬ ====================

    def save_style(self, user_id: int, style: str):
        """Сохранить стиль постов."""
        self.db.execute(
            "DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Стиль:%'",
            (user_id,)
        )
        self.memory.store_fact(user_id, f"Стиль: {style}", importance=1.0)

    def save_channel(self, user_id: int, channel_id: str, channel_name: str):
        """Сохранить канал клиента."""
        self.db.execute(
            "DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Канал:%'",
            (user_id,)
        )
        self.memory.store_fact(
            user_id,
            f"Канал: {channel_name} (ID: {channel_id})",
            importance=1.0
        )

    def add_competitor(self, user_id: int, channel: str, auto_analyze: bool = True):
        """Добавить канал конкурента и проанализировать через Executor."""
        # Генерируем алиасы для поиска (архитектурно)
        aliases = self._generate_channel_aliases(channel)

        self.memory.store(
            user_id=user_id,
            content=f"Конкурент: {channel}",
            memory_type=MemoryType.FACT,
            importance=0.8,
            metadata={"aliases": aliases, "channel": channel}
        )

        print(f"[Memory] Добавлен конкурент {channel}, алиасы: {aliases[:3]}...")

        if auto_analyze:
            self._analyze_channel_via_executor(user_id, channel)

    def _generate_channel_aliases(self, channel: str) -> List[str]:
        """Генерация алиасов канала для поиска (архитектурно)."""
        # Извлекаем название без @
        name = channel.replace('@', '').lower()

        aliases = [name]

        # Добавляем варианты транслитерации
        aliases.extend(self._get_translit_variants(name))

        # Добавляем русскую транслитерацию (латиница → кириллица)
        rus_translit = self._translit_to_russian(name)
        if rus_translit:
            aliases.append(rus_translit)

        # Убираем дубликаты
        return list(set(aliases))

    def _translit_to_russian(self, text: str) -> str:
        """Транслитерация латиницы в кириллицу (архитектурно)."""
        # Сначала многобуквенные сочетания
        multi_map = {
            'sch': 'щ', 'sh': 'ш', 'ch': 'ч', 'zh': 'ж', 'ts': 'ц',
            'yu': 'ю', 'ya': 'я', 'yo': 'ё', 'ye': 'е',
            'ow': 'оу', 'ew': 'ью', 'oo': 'у', 'ee': 'и',
        }
        result = text.lower()
        for lat, rus in multi_map.items():
            result = result.replace(lat, rus)

        # Потом однобуквенные
        single_map = {
            'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф',
            'g': 'г', 'h': 'х', 'i': 'и', 'j': 'дж', 'k': 'к', 'l': 'л',
            'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'q': 'к', 'r': 'р',
            's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс',
            'y': 'й', 'z': 'з',
        }
        for lat, rus in single_map.items():
            result = result.replace(lat, rus)

        return result

    def _analyze_channel_via_executor(self, user_id: int, channel: str) -> bool:
        """
        Анализ канала через архитектуру Executor → Plan → Steps.

        Plan (smm_analyze):
        1. TOOL_CALL: parse_channel
        2. LLM_CALL: smm_analyze_style
        3. TOOL_CALL: memory_store

        Returns: True если успешно, False если ошибка
        """
        print(f"\n[Executor] === Анализ {channel} ===")

        try:
            # Создаём задачу
            task = self.tasks.enqueue(
                user_id=user_id,
                task_type="smm_analyze",
                input_text=channel,
                input_data={
                    "user_id": user_id,
                    "channel": channel,
                }
            )

            # Напрямую переводим в running
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            lease_expires = now + timedelta(seconds=300)

            self.db.execute(
                """UPDATE tasks
                   SET status = 'running', locked_by = ?, locked_at = ?,
                       lease_expires_at = ?, started_at = ?, updated_at = ?
                   WHERE id = ?""",
                ("smm_agent", now.isoformat(), lease_expires.isoformat(),
                 now.isoformat(), now.isoformat(), task.id)
            )

            running_task = self.tasks.get_task(task.id)
            if running_task:
                self.executor.run_task(running_task)

            print(f"[Executor] === Готово: {channel} ===\n")
            return True

        except Exception as e:
            print(f"[Executor] Ошибка анализа {channel}: {e}")
            return False

    def add_news_source(self, user_id: int, url: str, name: str = ""):
        """Добавить источник новостей."""
        source_name = name or url
        self.memory.store_fact(
            user_id,
            f"Источник: {source_name} | {url}",
            importance=0.8
        )

    def get_news_sources(self, user_id: int) -> list:
        """Получить источники новостей пользователя."""
        facts = self.memory.get_facts(user_id)
        sources = []
        for f in facts:
            if f.content.startswith("Источник:"):
                parts = f.content.replace("Источник:", "").strip().split(" | ")
                if len(parts) == 2:
                    sources.append({"name": parts[0], "url": parts[1]})
                else:
                    sources.append({"name": parts[0], "url": parts[0]})
        return sources

    def remove_news_source(self, user_id: int, url: str):
        """Удалить источник."""
        self.db.execute(
            "DELETE FROM memory_items WHERE user_id = ? AND content LIKE ?",
            (user_id, f"%{url}%")
        )

    def get_competitors(self, user_id: int) -> List[str]:
        """Получить список конкурентов."""
        facts = self.memory.get_facts(user_id)
        competitors = []
        for f in facts:
            if f.content.startswith("Конкурент:"):
                ch = f.content.replace("Конкурент:", "").strip()
                competitors.append(ch)
        return competitors

    def get_competitors_with_ids(self, user_id: int) -> List[dict]:
        """Получить список конкурентов с ID для удаления."""
        rows = self.db.fetch_all(
            """SELECT id, content FROM memory_items
               WHERE user_id = ? AND content LIKE 'Конкурент:%'""",
            (user_id,)
        )
        result = []
        for row in rows:
            channel = row[1].replace("Конкурент:", "").strip()
            result.append({"id": row[0], "channel": channel})
        return result

    def remove_competitor(self, memory_id: int):
        """Удалить конкурента по ID."""
        self.db.execute("DELETE FROM memory_items WHERE id = ?", (memory_id,))

    def save_successful_post(self, user_id: int, post_text: str, metrics: dict = None):
        """Сохранить удачный пост."""
        content = f"Удачный пост: {post_text[:200]}"
        if metrics:
            content += f" | Просмотры: {metrics.get('views', '?')}"
        self.memory.store_decision(user_id, content, importance=0.8)

    def save_feedback(self, user_id: int, feedback: str, post_text: str = ""):
        """Сохранить фидбек для обучения."""
        content = f"Фидбек: {feedback}"
        if post_text:
            content += f" | Пост: {post_text[:100]}"
        self.memory.store(
            user_id=user_id,
            content=content,
            memory_type=MemoryType.FEEDBACK,
            importance=0.9
        )

    def get_channel_id(self, user_id: int) -> Optional[str]:
        """Получить ID канала пользователя."""
        facts = self.memory.get_facts(user_id)
        for f in facts:
            if "Канал:" in f.content and "ID:" in f.content:
                match = re.search(r'ID: ([^\)]+)', f.content)
                if match:
                    return match.group(1)
        return None

    def get_base_style(self, user_id: int) -> str:
        """Получить базовый стиль из настроек."""
        style = self.db.fetch_one(
            "SELECT content FROM memory_items WHERE user_id = ? AND content LIKE 'Стиль:%' ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
        if style:
            return style[0].replace('Стиль:', '').strip()
        return ""

    def get_recommended_temperature(self, user_id: int) -> float:
        """
        Получить рекомендованную температуру из анализа каналов.

        Приоритет:
        1. Собственный канал пользователя
        2. Среднее по конкурентам
        3. Default 0.5
        """
        import json

        # 1. Ищем собственный канал
        own_channel = self.get_channel_id(user_id)
        if own_channel:
            own_channel_clean = own_channel.replace('@', '')
            row = self.db.fetch_one(
                """SELECT metadata FROM memory_items
                   WHERE user_id = ?
                   AND content LIKE ?
                   AND metadata IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, f"Стиль канала %{own_channel_clean}%")
            )
            if row and row[0]:
                try:
                    meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    temp = meta.get("recommended_temperature")
                    if temp:
                        print(f"[Temperature] Из собственного канала {own_channel}: {temp}")
                        return float(temp)
                except (json.JSONDecodeError, TypeError):
                    pass

        # 2. Среднее по конкурентам
        rows = self.db.fetch_all(
            """SELECT metadata FROM memory_items
               WHERE user_id = ?
               AND content LIKE 'Стиль канала%'
               AND metadata IS NOT NULL
               ORDER BY created_at DESC LIMIT 5""",
            (user_id,)
        )
        temps = []
        for row in rows:
            if row[0]:
                try:
                    meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    temp = meta.get("recommended_temperature")
                    if temp:
                        temps.append(float(temp))
                except (json.JSONDecodeError, TypeError):
                    pass

        if temps:
            avg_temp = sum(temps) / len(temps)
            print(f"[Temperature] Среднее по {len(temps)} каналам: {avg_temp:.2f}")
            return round(avg_temp, 2)

        # 3. Default
        print("[Temperature] Нет данных, используем default 0.5")
        return 0.5

    def _find_relevant_channel_styles(self, user_id: int, topic: str, limit: int = 3) -> List[str]:
        """
        Найти релевантные стили каналов по теме поста (архитектурно, через FTS5).

        Если тема "ретроградный меркурий" — найдёт стили каналов про астрологию.
        Если тема "инвестиции в акции" — найдёт стили каналов про финансы.

        Возвращает до `limit` самых релевантных стилей.
        """
        # Извлекаем ключевые слова из темы (без стоп-слов)
        stop_words = {'и', 'в', 'на', 'с', 'что', 'это', 'как', 'а', 'не', 'но', 'для', 'по',
                      'пост', 'про', 'о', 'об', 'напиши', 'сделай', 'создай', 'тему', 'тема'}
        words = re.findall(r'\b[а-яА-ЯёЁa-zA-Z]{3,}\b', topic.lower())
        keywords = [w for w in words if w not in stop_words][:5]

        if not keywords:
            return []

        # Ищем в FTS5 среди стилей каналов
        search_query = " OR ".join(keywords)

        try:
            results = self.db.fetch_all(
                """SELECT m.content FROM memory_items m
                   JOIN memory_fts f ON m.id = f.rowid
                   WHERE m.user_id = ?
                   AND m.content LIKE 'Стиль канала%'
                   AND memory_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (user_id, search_query, limit)
            )
            if results:
                print(f"[Context] Найдено {len(results)} релевантных стилей по теме: {keywords}")
                return [r[0] for r in results]
        except Exception as e:
            print(f"[Context] FTS5 поиск не сработал: {e}")

        return []

    def _extract_competitor_insights(self, style_text: str, channel: str = "") -> str:
        """
        Извлечь ИНСАЙТЫ из анализа стиля конкурента, БЕЗ навязывания стиля.

        ОСТАВЛЯЕМ (инсайты — ЧТО работает):
        - Темы которые заходят
        - HOOKS — примеры цепляющих фраз
        - Триггеры вовлечения
        - Фирменные приёмы (идеи, не форматирование)

        УБИРАЕМ (стиль — КАК писать):
        - ЛИЦО ПОВЕСТВОВАНИЯ — это должен быть стиль пользователя
        - СТРУКТУРА абзацев — не копируем
        - Эмодзи-паттерны — не навязываем
        - ДЛИНА постов — у пользователя своя
        - TONE OF VOICE — это стиль, не инсайт
        """
        if not style_text:
            return ""

        insights = []

        # Разбиваем на секции
        lines = style_text.split('\n')

        current_section = ""
        keep_sections = ['hook', 'темы', 'триггер', 'вовлечен', 'приём', 'прием', 'фишк', 'работает', 'заход']
        skip_sections = ['лицо', 'структур', 'длина', 'эмодзи', 'emoji', 'tone', 'тон', 'формат', 'концовк']

        for line in lines:
            line_lower = line.lower().strip()

            # Определяем секцию
            if any(s in line_lower for s in skip_sections):
                current_section = "skip"
            elif any(s in line_lower for s in keep_sections):
                current_section = "keep"
                insights.append(line.strip())
            elif current_section == "keep" and line.strip():
                # Продолжаем добавлять строки из нужной секции
                insights.append(line.strip())
            elif current_section != "skip" and line.strip():
                # Неизвестная секция — проверяем на полезные ключевые слова
                if any(word in line_lower for word in ['пример', 'фраз', 'работает', 'цепля', 'вовлека']):
                    insights.append(line.strip())

        if not insights:
            # Fallback — берём только примеры фраз если есть
            for line in lines:
                if '•' in line or '—' in line or line.strip().startswith('-'):
                    # Это скорее всего пример
                    if len(line.strip()) > 20:
                        insights.append(line.strip())

        result = "\n".join(insights[:10])  # Максимум 10 строк

        if result:
            return f"ИНСАЙТЫ (темы и идеи, НЕ стиль):\n{result}\n\n⚠️ Пиши в СВОЁМ стиле, не копируй {channel}!"

        return ""

    def _extract_channel_from_topic(self, topic: str, user_id: int = None) -> Optional[str]:
        """
        Извлечь канал из темы поста (архитектурно, без LLM).

        Паттерны:
        - "в стиле @channel"
        - "как @channel"
        - "в стиле мегамаркета" → ищем в памяти
        - "@channel"
        """
        topic_lower = topic.lower()

        # 1. Ищем явный @channel
        match = re.search(r'@([\w_]+)', topic)
        if match:
            return f"@{match.group(1)}"

        # 2. Ищем "в стиле X", "как X" и проверяем в памяти
        patterns = [
            r'в стиле\s+(\S+)',
            r'как\s+у?\s*(\S+)',
            r'стиль\s+(\S+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, topic_lower)
            if match:
                keyword = match.group(1).strip('.,!?')

                # Пропускаем общие слова
                skip_words = {'этого', 'того', 'канала', 'поста', 'текста', 'обычно', 'всегда'}
                if keyword in skip_words:
                    continue

                # Если есть user_id — ищем канал в памяти по ключевому слову
                if user_id:
                    found_channel = self._find_channel_by_keyword(user_id, keyword)
                    if found_channel:
                        print(f"[Context] Найден канал '{found_channel}' по слову '{keyword}'")
                        return found_channel

                # Fallback: если слово похоже на канал
                if keyword.startswith('@'):
                    return keyword

        return None

    def _find_channel_by_keyword(self, user_id: int, keyword: str) -> Optional[str]:
        """Найти канал в памяти по ключевому слову (архитектурно)."""
        import json

        # Ищем среди конкурентов и стилей каналов
        rows = self.db.fetch_all(
            """SELECT content, metadata FROM memory_items
               WHERE user_id = ?
               AND (content LIKE 'Конкурент:%' OR content LIKE 'Стиль канала%')""",
            (user_id,)
        )

        keyword_lower = keyword.lower()
        keyword_translit = self._translit(keyword_lower)

        for content, metadata_str in rows:
            content_lower = content.lower()

            # 1. Проверяем алиасы из metadata (быстрый путь)
            if metadata_str:
                try:
                    metadata = json.loads(metadata_str)
                    aliases = metadata.get("aliases", [])
                    channel = metadata.get("channel", "")

                    # Проверяем совпадение с алиасами
                    for alias in aliases:
                        if (keyword_lower in alias or
                            alias in keyword_lower or
                            keyword_translit in alias or
                            self._fuzzy_match(keyword_lower, alias)):
                            print(f"[Context] Найден по алиасу: {keyword} → {channel}")
                            return channel
                except:
                    pass

            # 2. Fallback: поиск по content (для старых записей без metadata)
            channel_match = re.search(r'@([\w_]+)', content)
            channel_name = channel_match.group(1).lower() if channel_match else ""

            match_found = (
                keyword_lower in content_lower or
                keyword_lower in channel_name or
                keyword_translit in channel_name or
                self._translit(channel_name) in keyword_lower or
                self._fuzzy_match(keyword_lower, channel_name)
            )

            if match_found:
                if channel_match:
                    return f"@{channel_match.group(1)}"
                if 'конкурент:' in content_lower:
                    return content.replace('Конкурент:', '').strip()

        return None

    def _translit(self, text: str) -> str:
        """Транслитерация кириллицы в латиницу (архитектурно)."""
        translit_map = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        }
        result = ""
        for char in text.lower():
            result += translit_map.get(char, char)
        return result

    def _fuzzy_match(self, keyword: str, channel: str) -> bool:
        """Нечёткое сравнение (архитектурно) — проверяет похожесть."""
        if not keyword or not channel:
            return False

        # Убираем разделители
        kw = keyword.replace('_', '').replace('-', '').replace(' ', '')
        ch = channel.replace('_', '').replace('-', '').replace(' ', '')

        # 1. Прямое вхождение
        if kw in ch or ch in kw:
            return True

        # 2. Вариации транслитерации (оу/ow, у/u/ou)
        kw_variants = self._get_translit_variants(kw)
        for variant in kw_variants:
            if variant in ch or ch in variant:
                return True

        # 3. Минимум 60% общих символов (для коротких слов)
        if len(kw) >= 4 and len(ch) >= 4:
            common = sum(1 for c in kw if c in ch)
            similarity = common / max(len(kw), len(ch))
            if similarity >= 0.6:
                return True

        return False

    def _get_translit_variants(self, text: str) -> List[str]:
        """Генерация вариантов транслитерации (архитектурно)."""
        variants = [text]

        # Частые замены
        replacements = [
            ('ou', 'ow'), ('ow', 'ou'),
            ('u', 'ou'), ('ou', 'u'),
            ('k', 'c'), ('c', 'k'),
            ('ks', 'x'), ('x', 'ks'),
            ('i', 'y'), ('y', 'i'),
            ('ph', 'f'), ('f', 'ph'),
        ]

        for old, new in replacements:
            if old in text:
                variants.append(text.replace(old, new))

        return variants

    def build_smm_context(self, user_id: int, extra_style: str = "", target_channel: str = None, topic: str = None) -> str:
        """
        Собрать контекст для генерации:
        1. Стиль = база + дополнения
        2. Примеры успешных постов
        3. Инсайты из анализа конкурентов (релевантный по теме или конкретный)
        4. Фидбек

        Args:
            target_channel: если указан — использовать ТОЛЬКО стиль этого канала
            topic: тема поста — для поиска релевантного стиля через FTS5
        """
        parts = []

        # 1. СТИЛЬ
        base_style = self.get_base_style(user_id)
        if base_style or extra_style:
            style_text = base_style
            if extra_style:
                style_text = f"{base_style}. Дополнительно: {extra_style}" if base_style else extra_style
            parts.append(f"СТИЛЬ:\n{style_text}")

        # 2. ПРИМЕРЫ УСПЕШНЫХ ПОСТОВ
        published = self.db.fetch_all(
            """SELECT content FROM memory_items
               WHERE user_id = ?
               AND (content LIKE 'Опубликованный пост:%' OR content LIKE 'Удачный пост:%')
               ORDER BY created_at DESC LIMIT 5""",
            (user_id,)
        )
        if published:
            examples = []
            for row in published:
                text = row[0]
                for prefix in ['Опубликованный пост:', 'Удачный пост:']:
                    text = text.replace(prefix, '').strip()
                if '|' in text:
                    text = text.split('|')[0].strip()
                examples.append(f"• {text[:400]}")
            parts.append(f"ПРИМЕРЫ ПОСТОВ КОТОРЫЕ ЗАШЛИ:\n" + "\n".join(examples))

        # 3. СТИЛИ КАНАЛОВ — приоритет: собственный канал > конкуренты

        # 3.1 СОБСТВЕННЫЙ КАНАЛ — главный приоритет
        own_channel = self.get_channel_id(user_id)
        own_channel_style = None
        if own_channel:
            own_channel_clean = own_channel.replace('@', '')
            own_style_row = self.db.fetch_one(
                """SELECT content FROM memory_items
                   WHERE user_id = ?
                   AND (content LIKE ? OR content LIKE ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, f"Стиль канала %{own_channel_clean}%", f"Авто-анализ:%")
            )
            if own_style_row:
                own_channel_style = own_style_row[0][:600]
                parts.append(f"ТВОЙ СТИЛЬ (ГЛАВНЫЙ ПРИОРИТЕТ — пиши так!):\n{own_channel_style}")
                print(f"[Context] Собственный канал: {own_channel}")

        # 3.2 Конкретный канал для вдохновения (если указан)
        # ВАЖНО: извлекаем только ИНСАЙТЫ, не весь стиль!
        if target_channel:
            channel_clean = target_channel.replace('@', '')
            channel_style = self.db.fetch_one(
                """SELECT content FROM memory_items
                   WHERE user_id = ?
                   AND (content LIKE ? OR content LIKE ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, f"Стиль канала %{channel_clean}%", f"Стиль канала @{channel_clean}%")
            )
            if channel_style:
                # Извлекаем только инсайты, не весь стиль
                insights = self._extract_competitor_insights(channel_style[0], target_channel)
                if insights:
                    parts.append(insights)
                    print(f"[Context] Инсайты из {target_channel} (без копирования стиля)")

        # 3.3 Релевантные конкуренты по теме — только ИНСАЙТЫ
        # Ищем конкурентов по теме через FTS5, но берём только идеи/темы
        if not target_channel and topic:
            relevant_styles = self._find_relevant_channel_styles(user_id, topic, limit=2)
            if relevant_styles:
                all_insights = []
                for style in relevant_styles:
                    # Извлекаем инсайты без навязывания стиля
                    insight = self._extract_competitor_insights(style, "конкурентов")
                    if insight:
                        all_insights.append(insight)
                if all_insights:
                    parts.append("\n---\n".join(all_insights[:2]))
                    print(f"[Context] FTS5: найдено {len(all_insights)} источников инсайтов")

        # 4. ТИПИЧНЫЕ ПРАВКИ КЛИЕНТА
        edits = self.db.fetch_all(
            """SELECT content FROM memory_items
               WHERE user_id = ?
               AND (content LIKE 'Фидбек:%' OR content LIKE 'Пример правки:%')
               ORDER BY created_at DESC LIMIT 10""",
            (user_id,)
        )
        if edits:
            patterns = self._analyze_edit_patterns([row[0] for row in edits])
            if patterns:
                parts.append(f"ВАЖНО — КЛИЕНТ ОБЫЧНО ПРОСИТ:\n{patterns}")

        return "\n\n".join(parts) if parts else ""

    def _analyze_edit_patterns(self, edits: list) -> str:
        """Анализ типичных правок клиента."""
        counters = {
            'short': 0, 'long': 0, 'emoji_add': 0, 'emoji_remove': 0,
            'simple': 0, 'bold': 0, 'official': 0, 'soft': 0,
            'structure': 0, 'cta': 0
        }

        keywords = {
            'short': ['короч', 'сократ', 'меньше текст', 'убери лишн', 'компактн'],
            'long': ['длинн', 'больше текст', 'разверн', 'подробн', 'добавь'],
            'emoji_add': ['добавь эмодзи', 'эмодзи', 'смайл'],
            'emoji_remove': ['без эмодзи', 'убери эмодзи', 'убери смайл'],
            'simple': ['проще', 'понятн', 'легче'],
            'bold': ['дерзк', 'дерзч', 'провокац', 'жёстч', 'жестч', 'смел'],
            'official': ['официальн', 'формальн', 'серьёзн', 'серьезн'],
            'soft': ['мягче', 'нежн', 'аккуратн'],
            'structure': ['структур', 'списк', 'пункт', 'раздел'],
            'cta': ['призыв', 'call to action', 'действи']
        }

        for edit in edits:
            edit_lower = edit.lower()
            for category, words in keywords.items():
                if any(w in edit_lower for w in words):
                    counters[category] += 1

        insights = []
        if counters['short'] >= 2:
            insights.append("• Пиши КОРОЧЕ — клиент часто просит сократить")
        if counters['long'] >= 2:
            insights.append("• Пиши ДЛИННЕЕ и подробнее")
        if counters['emoji_add'] >= 2 and counters['emoji_remove'] < 2:
            insights.append("• Добавляй эмодзи")
        if counters['emoji_remove'] >= 1:
            insights.append("• БЕЗ эмодзи")
        if counters['simple'] >= 2:
            insights.append("• Пиши ПРОЩЕ и понятнее")
        if counters['bold'] >= 1:
            insights.append("• Тон дерзкий, провокационный, смелый")
        if counters['official'] >= 1:
            insights.append("• Тон официальный, серьёзный")
        if counters['soft'] >= 1:
            insights.append("• Тон мягкий, аккуратный")
        if counters['structure'] >= 1:
            insights.append("• Используй структуру: списки, пункты")
        if counters['cta'] >= 1:
            insights.append("• Добавляй призыв к действию")

        return "\n".join(insights) if insights else ""

    # ==================== ГЕНЕРАЦИЯ ЧЕРЕЗ EXECUTOR ====================

    def generate_post(self, user_id: int, topic: str, style: str = None) -> PostDraft:
        """
        Сгенерировать пост через Executor → Plan → Steps.

        Plan (smm_generate):
        1. TOOL_CALL: memory_search — поиск похожих
        2. TOOL_CALL: web_search — актуальная инфа
        3. LLM_CALL: smm_generate_post — генерация
        4. APPROVAL — пауза для пользователя

        Возвращает PostDraft с текстом и task_id.
        """
        print(f"\n[Executor] === Генерация поста ===")
        print(f"[Executor] Тема: '{topic}'")

        # Извлекаем канал из темы (если указан) — ищем и по словам в памяти
        target_channel = self._extract_channel_from_topic(topic, user_id=user_id)
        if target_channel:
            print(f"[Executor] Целевой канал: {target_channel}")

        # Собираем SMM контекст (с фильтром по каналу или поиск релевантного по теме)
        smm_context = self.build_smm_context(
            user_id,
            extra_style=style or "",
            target_channel=target_channel,
            topic=topic if not target_channel else None  # Если канал указан явно — не ищем по теме
        )

        # Определяем нужен ли web search
        skip_web_search = not self._needs_research(topic)

        # Получаем рекомендованную температуру из анализа каналов
        recommended_temp = self.get_recommended_temperature(user_id)

        # Создаём задачу
        task = self.tasks.enqueue(
            user_id=user_id,
            task_type="smm_generate",
            input_text=topic,
            input_data={
                "user_id": user_id,
                "topic": topic,
                "smm_context": smm_context,
                "skip_web_search": skip_web_search,
                "recommended_temperature": recommended_temp,
            }
        )

        print(f"[Executor] Task #{task.id} создан")

        # Переводим задачу в running и запускаем Executor
        draft_text = ""
        try:
            # Напрямую переводим в running (без claim из общей очереди)
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            lease_expires = now + timedelta(seconds=300)

            self.db.execute(
                """UPDATE tasks
                   SET status = 'running', locked_by = ?, locked_at = ?,
                       lease_expires_at = ?, started_at = ?, updated_at = ?
                   WHERE id = ?""",
                ("smm_agent", now.isoformat(), lease_expires.isoformat(),
                 now.isoformat(), now.isoformat(), task.id)
            )

            running_task = self.tasks.get_task(task.id)
            if running_task:
                print(f"[Executor] Task #{task.id} running")
                self.executor.run_task(running_task)
        except ApprovalRequired as e:
            # Это нормальный flow — задача остановилась на APPROVAL
            draft_text = e.draft_content or ""
            print(f"[Executor] Task #{task.id} paused for approval")
        except Exception as e:
            print(f"[Executor] Error: {e}")
            import traceback
            traceback.print_exc()

        # Если draft пустой — пробуем достать из task events
        if not draft_text:
            draft_text = self._get_draft_from_task(task.id)

        print(f"[Executor] === Готово ===\n")

        return PostDraft(
            text=draft_text,
            topic=topic,
            task_id=task.id,
            channel_id=self.get_channel_id(user_id) or ""
        )

    def _get_draft_from_task(self, task_id: int) -> str:
        """Извлечь draft из step_results задачи."""
        # Ищем в task_events событие с draft_content
        rows = self.db.fetch_all(
            """SELECT event_data FROM task_events
               WHERE task_id = ? AND event_type = 'paused'
               ORDER BY created_at DESC LIMIT 1""",
            (task_id,)
        )
        if rows:
            import json
            try:
                data = json.loads(rows[0][0])
                return data.get("draft_content", "")
            except:
                pass

        # Fallback: ищем в task_steps
        rows = self.db.fetch_all(
            """SELECT result FROM task_steps
               WHERE task_id = ? AND action = 'llm_call'
               ORDER BY step_index DESC LIMIT 1""",
            (task_id,)
        )
        if rows and rows[0][0]:
            import json
            try:
                data = json.loads(rows[0][0])
                return data.get("response", "")
            except:
                pass

        return ""

    def _needs_research(self, topic: str) -> bool:
        """Определить, нужен ли поиск актуальной информации.

        Всегда возвращаем True — интернет-поиск улучшает качество постов,
        добавляет актуальные факты и проверяет информацию.
        """
        # Всегда ищем в интернете для максимального качества
        return True

    # ==================== РЕДАКТИРОВАНИЕ ====================

    def edit_post(self, user_id: int, original: str, edit_request: str, topic: str = "") -> str:
        """
        Отредактировать пост (гибридный подход v7).

        Архитектура:
        1. Разбиваем запрос на части
        2. Классифицируем: precise (код) или creative (LLM)
        3. Сначала применяем ВСЕ precise операции
        4. Потом creative (LLM возвращает готовый текст)
        """
        print(f"[Edit] Запрос: {edit_request}")

        # 1. Разбиваем запрос на части
        parts = self._split_edit_request(edit_request)
        print(f"[Edit] Части запроса: {parts}")

        # 2. Классифицируем каждую часть
        precise_parts = []
        creative_parts = []

        for part in parts:
            if self._is_precise_edit(part):
                precise_parts.append(part)
                print(f"[Edit]   precise: {part}")
            else:
                creative_parts.append(part)
                print(f"[Edit]   creative: {part}")

        # 3. Сначала применяем ВСЕ precise операции
        result = original
        for part in precise_parts:
            result = self._precise_edit(result, part)

        # 4. Потом creative (если есть)
        if creative_parts:
            creative_request = ", ".join(creative_parts)
            result = self._creative_edit(user_id, result, creative_request, topic)

        # 5. Нормализация
        result = re.sub(r'\n{3,}', '\n\n', result)
        result = re.sub(r'[ \t]+\n', '\n', result)
        # Убираем неподдерживаемые теги (span, div, style и т.д.)
        result = re.sub(r'<span[^>]*>', '', result)
        result = re.sub(r'</span>', '', result)
        result = re.sub(r'<div[^>]*>', '', result)
        result = re.sub(r'</div>', '', result)
        result = result.strip()
        result = _markdown_to_html(result)

        self._save_edit_feedback(user_id, edit_request, original, result)
        return result

    def _split_edit_request(self, request: str) -> list:
        """Разбить запрос на отдельные команды."""
        # Защищаем "N и M" (числа) от разбиения
        protected = re.sub(r'(\d+)\s+и\s+(\d+)', r'\1__AND__\2', request)

        # Разделители: "и", "а также", "ещё", "плюс", запятые
        separators = r'\s+и\s+|\s+а\s+также\s+|\s+ещё\s+|\s+еще\s+|\s+также\s+|\s+плюс\s+|,\s*'
        parts = re.split(separators, protected, flags=re.IGNORECASE)

        # Восстанавливаем "N и M"
        parts = [p.replace('__AND__', ' и ') for p in parts]

        # Убираем пустые и слишком короткие
        parts = [p.strip() for p in parts if p and len(p.strip()) > 2]

        if not parts:
            return [request]

        # Добавляем контекст — если нет глагола, берём из предыдущей команды
        result = []
        last_verb = "убери"
        verbs = ['выдели', 'убери', 'удали', 'добавь', 'замени', 'сделай', 'поменяй', 'перепиши', 'напиши', 'разбей', 'измени', 'исправь', 'улучши', 'проверь', 'отредактируй']

        for part in parts:
            part_lower = part.lower()
            has_verb = any(v in part_lower for v in verbs)

            if has_verb:
                for v in verbs:
                    if v in part_lower:
                        last_verb = v
                        break
                result.append(part)
            else:
                # Нет глагола — добавляем из контекста
                result.append(f"{last_verb} {part}")

        return result if result else [request]

    def _is_precise_edit(self, request: str) -> bool:
        """Проверить, можно ли выполнить запрос кодом (без LLM)."""
        request_lower = request.lower()

        # "чёрным/черным" = "жирным" (голосовой ввод)
        request_lower = request_lower.replace('чёрн', 'жирн').replace('черн', 'жирн')
        # "смайлики/смайлы" = "эмодзи"
        request_lower = re.sub(r'смайлик\w*', 'эмодзи', request_lower)
        request_lower = re.sub(r'смайл\w*', 'эмодзи', request_lower)

        # Названия эмодзи (для распознавания "убери радугу" без слова "эмодзи")
        emoji_names_pattern = r'(радуг|солнц|сердц|сердеч|огон|огонёк|звезд|звёзд|цвет|роз|ракет|молни|дом|домик|ключ|гаечн)'

        # Точечные операции — детектируем по ключевым словам
        precise_patterns = [
            r'убери\s+эмодзи',
            r'удали\s+эмодзи',
            r'убери\s+все\s+эмодзи',
            r'без\s+эмодзи',
            r'убери\s+.*' + emoji_names_pattern,  # "убери радугу", "убери там эту радугу"
            r'удали\s+.*' + emoji_names_pattern,
            r'убери\s+(первый|последний|второй|третий|четвертый|четвёртый|\d+[-]?й?)\s*абзац',
            r'удали\s+(первый|последний|второй|третий|четвертый|четвёртый|\d+[-]?й?)\s*абзац',
            r'убери\s+последни[ей]\s+(два|три|четыре|\d+)\s*абзац',
            r'удали\s+последни[ей]\s+(два|три|четыре|\d+)\s*абзац',
            r'выдели.*жирн',
            r'сделай.*жирн',
            r'убери\s+жирн',
            r'без\s+жирн',
            r'убери\s+хештег',
            r'убери\s+хэштег',
            r'удали\s+хештег',
            r'удали\s+хэштег',
            r'без\s+хештег',
            r'без\s+хэштег',
            r'замени\s+.+\s+на\s+',
            r'вместо\s+.+\s+(поставь|сделай|вставь)',
        ]

        for pattern in precise_patterns:
            if re.search(pattern, request_lower):
                return True

        return False

    def _resolve_emoji_by_name(self, name: str) -> str:
        """Использует LLM чтобы определить эмодзи по названию."""
        prompt = f"""Какой эмодзи соответствует слову "{name}"?
Ответь ТОЛЬКО одним эмодзи, без текста. Если не знаешь — ответь пустой строкой."""

        try:
            response = self.llm.complete(
                messages=[Message.user(prompt)],
                user_id=0,
                temperature=0.0
            )
            emoji = response.content.strip()
            # Проверяем что это действительно эмодзи (1-2 символа Unicode)
            if len(emoji) <= 4 and any(ord(c) > 127 for c in emoji):
                print(f"[Edit] LLM resolved '{name}' → {emoji}")
                return emoji
        except Exception as e:
            print(f"[Edit] LLM emoji resolve failed: {e}")
        return ""

    def _precise_edit(self, text: str, request: str) -> str:
        """Применить точечную правку кодом (без LLM)."""
        result = text
        request_lower = request.lower()

        # Нормализация голосовых искажений
        request_lower = re.sub(r'смайлик\w*', 'эмодзи', request_lower)
        request_lower = re.sub(r'смайл\w*', 'эмодзи', request_lower)

        # === УБЕРИ ЭМОДЗИ ===
        if ('убери' in request_lower or 'удали' in request_lower or 'без' in request_lower) and \
           ('эмодзи' in request_lower or 'эмоджи' in request_lower):

            # Сначала проверяем — есть ли сам эмодзи в запросе?
            emoji_pattern = re.compile("[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]+")
            emojis_in_request = emoji_pattern.findall(request)

            found_specific = False
            for em in emojis_in_request:
                if em in result:
                    result = result.replace(em, '', 1)
                    print(f"[Edit] ✓ precise: убран эмодзи {em}")
                    found_specific = True

            # Ищем название эмодзи в запросе (используем LLM)
            if not found_specific:
                # Извлекаем слово-название после "эмодзи"
                name_match = re.search(r'эмодзи\s+(\w+)', request_lower)
                if name_match:
                    emoji_name = name_match.group(1)
                    # Спрашиваем LLM какой эмодзи соответствует названию
                    resolved_emoji = self._resolve_emoji_by_name(emoji_name)
                    if resolved_emoji and resolved_emoji in result:
                        result = result.replace(resolved_emoji, '', 1)
                        print(f"[Edit] ✓ precise: убран эмодзи {resolved_emoji} (LLM resolved '{emoji_name}')")
                        found_specific = True

            # Убрать ВСЕ эмодзи только если явно сказали "все эмодзи" или "убери эмодзи" без конкретики
            if not found_specific:
                if 'все' in request_lower:
                    result = emoji_pattern.sub('', result)
                    print(f"[Edit] ✓ precise: убраны все эмодзи")

        # === УБЕРИ АБЗАЦ ===
        paragraphs = [p for p in result.split('\n\n') if p.strip()]

        # Последний абзац
        if re.search(r'(убери|удали).*последн\w*\s*абзац', request_lower):
            if len(paragraphs) > 1:
                result = '\n\n'.join(paragraphs[:-1])
                print(f"[Edit] ✓ precise: удалён последний абзац")

        # Последние N абзацев
        last_n_match = re.search(r'(убери|удали).*последни[ех]\s+(два|три|четыре|\d+)\s*абзац', request_lower)
        if last_n_match:
            num_word = last_n_match.group(2)
            num_map = {'два': 2, 'три': 3, 'четыре': 4}
            n = num_map.get(num_word, int(num_word) if num_word.isdigit() else 1)
            paragraphs = [p for p in result.split('\n\n') if p.strip()]
            if len(paragraphs) > n:
                result = '\n\n'.join(paragraphs[:-n])
                print(f"[Edit] ✓ precise: удалены последние {n} абзацев")

        # Первый абзац
        if re.search(r'(убери|удали).*перв\w*\s*абзац', request_lower):
            paragraphs = [p for p in result.split('\n\n') if p.strip()]
            if len(paragraphs) > 1:
                result = '\n\n'.join(paragraphs[1:])
                print(f"[Edit] ✓ precise: удалён первый абзац")

        # N-ый абзац (второй, третий, четвертый)
        ordinals = {'втор': 2, 'трет': 3, 'четверт': 4, 'четвёрт': 4, 'пят': 5}
        for ordinal, idx in ordinals.items():
            if ordinal in request_lower and 'абзац' in request_lower:
                if 'убери' in request_lower or 'удали' in request_lower:
                    paragraphs = [p for p in result.split('\n\n') if p.strip()]
                    if idx <= len(paragraphs):
                        paragraphs.pop(idx - 1)
                        result = '\n\n'.join(paragraphs)
                        print(f"[Edit] ✓ precise: удалён {idx}-й абзац")
                    break

        # === ВЫДЕЛИ ЖИРНЫМ ===
        # "чёрным" = "жирным" (голосовой ввод)
        bold_request = request_lower.replace('черн', 'жирн').replace('чёрн', 'жирн')

        if re.search(r'(выдели|сделай).*жирн', bold_request):
            # Выделить первый абзац
            if re.search(r'перв\w*\s*абзац', bold_request):
                paragraphs = result.split('\n\n')
                if paragraphs and '<b>' not in paragraphs[0]:
                    paragraphs[0] = f'<b>{paragraphs[0]}</b>'
                    result = '\n\n'.join(paragraphs)
                    print(f"[Edit] ✓ precise: первый абзац жирным")

            # Выделить первое предложение
            elif re.search(r'перв\w*\s*предлож', bold_request):
                sentences = re.split(r'(?<=[.!?])\s+', result, maxsplit=1)
                if sentences and '<b>' not in sentences[0]:
                    result = f'<b>{sentences[0]}</b>'
                    if len(sentences) > 1:
                        result += '\n\n' + sentences[1]
                    print(f"[Edit] ✓ precise: первое предложение жирным")

            # Выделить конкретную фразу (число, процент)
            else:
                # Ищем что выделить в запросе
                phrase_match = re.search(r'выдел\w*\s+([^\s]+(?:\s+[^\s]+)?)\s+жирн', request_lower)
                if not phrase_match:
                    phrase_match = re.search(r'(\d+%?)', request)
                if phrase_match:
                    phrase = phrase_match.group(1).strip()
                    if phrase in result and f'<b>{phrase}</b>' not in result:
                        result = result.replace(phrase, f'<b>{phrase}</b>', 1)
                        print(f"[Edit] ✓ precise: выделено жирным: {phrase}")

        # === УБЕРИ ЖИРНЫЙ ===
        if re.search(r'(убери|без)\s*жирн', request_lower):
            if '<b>' in result or '**' in result:
                result = re.sub(r'</?b>', '', result)
                result = re.sub(r'\*\*([^*]+)\*\*', r'\1', result)
                print(f"[Edit] ✓ precise: убран жирный")
            else:
                print(f"[Edit] ℹ precise: жирного текста нет")

        # === УБЕРИ ХЕШТЕГИ ===
        if re.search(r'(убери|удали|без)\s*(хештег|хэштег)', request_lower):
            if '#' in result:
                result = re.sub(r'#\w+\s*', '', result)
                print(f"[Edit] ✓ precise: убраны хештеги")
            else:
                print(f"[Edit] ℹ precise: хештегов нет")

        # === ЗАМЕНА ===
        # Паттерны: "замени X на Y", "вместо X поставь Y", "X замени на Y"
        replace_match = re.search(r'замени\s+(.+?)\s+на\s+(.+?)(?:\s*$|\s*,)', request, re.IGNORECASE)
        if not replace_match:
            replace_match = re.search(r'вместо\s+(.+?)\s+(?:поставь|сделай|вставь)\s+(.+?)(?:\s*$|\s*,)', request, re.IGNORECASE)

        if replace_match:
            old_text, new_text = replace_match.group(1).strip(), replace_match.group(2).strip()

            # Проверяем, похоже ли old_text на название эмодзи
            # Если да — используем LLM для резолва
            emoji_pattern = re.compile("[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]+")

            # Резолвим old_text если это слово (не эмодзи)
            if not emoji_pattern.search(old_text):
                resolved_old = self._resolve_emoji_by_name(old_text)
                if resolved_old and resolved_old in result:
                    old_text = resolved_old

            # Резолвим new_text если это слово
            if not emoji_pattern.search(new_text):
                resolved_new = self._resolve_emoji_by_name(new_text)
                if resolved_new:
                    new_text = resolved_new

            if old_text in result:
                result = result.replace(old_text, new_text, 1)
                print(f"[Edit] ✓ precise: заменено '{old_text}' → '{new_text}'")

        return result

    def _creative_edit(self, user_id: int, original: str, request: str, topic: str) -> str:
        """LLM редактирует текст напрямую (возвращает готовый результат)."""

        # Нормализуем короткие команды в полноценные инструкции
        request_lower = request.lower().strip()
        short_commands = {
            'короче': 'сделай текст короче, убери лишние слова и повторы',
            'длиннее': 'сделай текст длиннее, добавь подробностей',
            'проще': 'сделай текст проще, используй более простые слова',
            'формальнее': 'сделай текст более формальным и официальным',
            'неформальнее': 'сделай текст более живым и разговорным',
            'жёстче': 'сделай текст более резким и прямолинейным',
            'мягче': 'сделай текст более мягким и дружелюбным',
        }
        for short, full in short_commands.items():
            if request_lower == short or request_lower == f'сделай {short}':
                request = full
                print(f"[Edit] Нормализация: '{request_lower}' → '{full}'")
                break

        # Контекст стиля из памяти
        style_hint = ""
        if topic:
            try:
                results = self.memory.search(user_id, f"стиль {topic}", limit=1)
                if results:
                    style_hint = f"\n\nСТИЛЬ КЛИЕНТА: {results[0].content[:200]}"
            except:
                pass

        # Нумеруем абзацы для контекста
        paragraphs = [p.strip() for p in original.split('\n\n') if p.strip()]
        numbered = "\n\n".join([f"[Абзац {i+1}] {p}" for i, p in enumerate(paragraphs)])

        # Определяем тип запроса для правильных инструкций
        request_lower = request.lower()
        is_shorten = any(w in request_lower for w in ['короче', 'сократи', 'убери лишнее', 'компактнее'])
        is_expand = any(w in request_lower for w in ['длиннее', 'подробнее', 'добавь деталей', 'разверни'])

        # Специальные инструкции для сокращения
        if is_shorten:
            # Агрессивный промпт для сокращения с объединением абзацев
            target_len = int(len(original) * 0.6)
            num_paragraphs = len(paragraphs)
            target_paragraphs = max(2, num_paragraphs // 2)  # Сократить число абзацев вдвое

            prompt = f"""ЗАДАЧА: СОКРАТИТЬ текст до ~{target_len} символов (сейчас {len(original)})

ТЕКСТ:
{original}

ПРАВИЛА СОКРАЩЕНИЯ:
1. ОБЪЕДИНЯЙ абзацы по смыслу — сейчас {num_paragraphs} абзацев, нужно {target_paragraphs}-{target_paragraphs+1}
2. Убери повторы и "воду", но СОХРАНИ ключевые мысли
3. Много мелких абзацев — плохо, лучше 2-4 плотных абзаца
4. Первый абзац = хук, последний = вопрос/призыв (если был)

Дополнительно: {request}{style_hint}

Верни ТОЛЬКО сокращённый текст, без комментариев."""
            print(f"[Edit] Creative mode (SHORTEN): {len(original)} → target ~{target_len}")
            response = self.llm.complete_simple(prompt, task_type="smm")
            result = response.strip()
        else:
            if is_expand:
                structure_rule = """- Добавляй примеры, детали, пояснения
- Можно разбивать абзацы для читаемости"""
            else:
                structure_rule = """- Структуру абзацев — не объединяй и не разбивай без необходимости"""

            prompt = f"""ТЕКСТ ПОСТА ({len(paragraphs)} абзацев):
{numbered}

ЗАПРОС: {request}{style_hint}

ПРАВИЛА:
- Работай ТОЛЬКО с текстом выше
{structure_rule}
- Сохрани ключевые факты (даты, цифры, имена)
- Используй ТОЛЬКО теги <b> для жирного
- НЕ используй span, div, style или другие HTML теги

Верни ТОЛЬКО готовый текст поста БЕЗ нумерации абзацев, без комментариев."""

            print(f"[Edit] Creative mode: {request}")
            response = self.llm.complete_simple(prompt, task_type="smm")
            result = response.strip()

        # Убираем markdown обёртки если есть
        if result.startswith("```"):
            result = re.sub(r'^```\w*\s*', '', result)
            result = re.sub(r'\s*```$', '', result)

        # Проверка адекватности результата
        request_lower = request.lower()

        # Определяем ожидаемые границы длины на основе запроса
        # ВАЖНО: "короче" имеет приоритет над "добавь эмодзи"
        shorten_words = ['сократи', 'короче', 'убери лишнее', 'компактнее']
        expand_words = ['длиннее', 'подробнее', 'разверни', 'увеличь', 'расширь текст']

        if any(word in request_lower for word in shorten_words):
            # Запрос на сокращение - результат должен быть короче
            max_multiplier = 1.1  # Небольшой запас
            min_multiplier = 0.1  # Можно сократить до 10%
        elif any(word in request_lower for word in expand_words):
            # Запрос на расширение текста - разрешаем до 5x
            max_multiplier = 5
            min_multiplier = 0.8
        else:
            # Обычное редактирование (добавь эмодзи, измени тон и т.д.)
            max_multiplier = 3
            min_multiplier = 0.3

        min_len = max(20, int(len(original) * min_multiplier))
        max_len = int(len(original) * max_multiplier)

        print(f"[Edit] Длина: оригинал={len(original)}, результат={len(result)}, границы={min_len}-{max_len}")

        if len(result) < min_len or len(result) > max_len:
            print(f"[Edit] ⚠️ Creative вернул странный результат, используем оригинал")
            return original

        print(f"[Edit] ✓ creative edit done, сокращение: {100 - int(len(result)/len(original)*100)}%")
        return result

    def _save_edit_feedback(self, user_id: int, edit_request: str, original: str, edited: str):
        """Сохранить фидбек о правке."""
        self.save_feedback(user_id, f"Правка: {edit_request}", original)
        self.memory.store(
            user_id=user_id,
            content=f"Пример правки: '{edit_request}' | Было: {original[:150]}... | Стало: {edited[:150]}...",
            memory_type=MemoryType.FEEDBACK,
            importance=0.85
        )

    def edit_post_with_history(self, user_id: int, current: str, edit_request: str, versions: list) -> str:
        """Редактировать пост с учётом истории версий.

        Использует гибридный edit_post() для обычных правок.
        Специальные команды (откат, верни оригинал) обрабатываются отдельно.
        """
        request_lower = edit_request.lower().strip()

        # Команды отката — обрабатываем без LLM
        if any(cmd in request_lower for cmd in ['верни оригинал', 'первый вариант', 'изначальн']):
            if versions:
                print(f"[Edit] Откат к оригиналу")
                return versions[0]
            return current

        if any(cmd in request_lower for cmd in ['откати', 'назад', 'верни предыдущ', 'отмени']):
            if len(versions) >= 2:
                print(f"[Edit] Откат к предыдущей версии")
                return versions[-2]
            return current

        # Для всех остальных правок — гибридный edit_post()
        return self.edit_post(user_id, current, edit_request, topic="")

    def approve_post(self, task_id: int, user_id: int, post_text: str):
        """Одобрить пост."""
        self.tasks.succeed(task_id, result={"text": post_text})
        self.save_successful_post(user_id, post_text)

    def reject_post(self, task_id: int, user_id: int, reason: str = ""):
        """Отклонить пост."""
        self.tasks.fail(task_id, error=reason or "rejected")
        if reason:
            self.save_feedback(user_id, f"Отклонено: {reason}")

    # ==================== АНАЛИЗ КОНКУРЕНТОВ ====================

    def _format_number(self, num: int) -> str:
        """Форматирует число: 1500 -> 1.5K, 1500000 -> 1.5M"""
        if num >= 1000000:
            return f"{num/1000000:.1f}M"
        elif num >= 1000:
            return f"{num/1000:.1f}K"
        return str(num)

    def _is_ad_post(self, text: str) -> bool:
        """Проверка на рекламный пост."""
        ad_markers = [
            '#реклама', '#ad', '#промо', '#promo', 'реклама',
            'переходи по ссылке', 'купить', 'скидка', 'промокод',
            'закажи', 'оплати', 'подписывайся', 'регистрируйся'
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in ad_markers)

    def analyze_single_channel(self, user_id: int, channel: str) -> tuple:
        """Анализ одного канала. Возвращает (raw_posts, analysis)."""
        try:
            # Получаем инфо о канале (подписчики)
            channel_info = self.parser.get_channel_info(channel)
            subscribers = channel_info.get('subscribers', 0)
            channel_title = channel_info.get('title', channel)

            posts = self.parser.get_top_posts(channel, limit=20)
            organic_posts = [p for p in posts if not self._is_ad_post(p.text)][:15]

            if not organic_posts:
                return "", f"Не найдено органических постов в {channel}"

            # Считаем метрики
            views = [p.views for p in organic_posts]
            reactions = [p.reactions for p in organic_posts]
            forwards = [p.forwards for p in organic_posts]

            avg_views = sum(views) // len(views) if views else 0
            avg_reactions = sum(reactions) // len(reactions) if reactions else 0
            avg_forwards = sum(forwards) // len(forwards) if forwards else 0
            max_views = max(views) if views else 0

            # Engagement rate
            engagement = 0
            if avg_views > 0:
                engagement = round((avg_reactions + avg_forwards) / avg_views * 100, 2)

            # Формируем статистику
            stats_text = (
                f"<b>📊 {channel_title}</b>\n"
                f"👥 Подписчики: <b>{self._format_number(subscribers)}</b>\n"
                f"👁 Просмотры: <b>{self._format_number(avg_views)}</b> (макс: {self._format_number(max_views)})\n"
                f"❤️ Реакции: <b>{self._format_number(avg_reactions)}</b> в среднем\n"
                f"🔄 Репосты: <b>{self._format_number(avg_forwards)}</b> в среднем\n"
                f"📈 Engagement: <b>{engagement}%</b>\n"
                f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
            )

            posts_list = []
            for p in organic_posts:
                # Добавляем дату чтобы LLM понимал актуальность
                date_str = p.date[:10] if p.date else ""  # YYYY-MM-DD
                posts_list.append(f"[{date_str}] 👁 {p.views}: {p.text[:200]}...")

            posts_text = "\n\n".join(posts_list)

            prompt = f"""Проанализируй топовые посты канала {channel}.

ПОСТЫ (отсортированы по просмотрам):
{posts_text}

Выдели:
1. ЛИЦО ПОВЕСТВОВАНИЯ — САМОЕ ВАЖНОЕ!
   - 1-е лицо ("я", "мы", "мне") или
   - 3-е лицо/безличный ("компания", "было решено")
2. Какие темы заходят лучше всего
3. Стиль написания (длина, тон, эмодзи)
4. Что делает эти посты популярными
5. 2-3 идеи для похожих постов

Начни с ЛИЦА — это критически важно для копирования стиля!
Кратко, по пунктам. Используй HTML-теги для форматирования: <b>жирный</b>"""

            response = self.llm.complete(
                messages=[
                    Message.system("Ты аналитик контента. Отвечай на русском. Сейчас 2026 год. Для выделения используй HTML: <b>жирный</b>"),
                    Message.user(prompt)
                ],
                user_id=user_id,
                max_tokens=2000
            )

            # Конвертируем markdown → HTML
            analysis = _markdown_to_html(response.content)

            # Полный анализ = статистика + AI-анализ
            full_analysis = stats_text + analysis

            # Сохраняем анализ (унифицированный формат)
            self.memory.store(
                user_id=user_id,
                content=f"Стиль канала {channel}: {analysis[:1500]}",
                memory_type=MemoryType.CONTEXT,
                importance=0.85,
                metadata={"channel": channel, "analysis_version": "v1"}
            )

            return posts_text, full_analysis

        except Exception as e:
            return "", f"Ошибка анализа {channel}: {e}"

    def analyze_competitors(self, user_id: int) -> tuple:
        """Анализ всех конкурентов."""
        competitors = self.get_competitors(user_id)
        if not competitors:
            return "", "Нет конкурентов. Добавь через /competitor @channel"

        all_results = []
        all_posts = []

        for channel in competitors[:5]:
            posts_text, analysis = self.analyze_single_channel(user_id, channel)
            if posts_text:
                all_posts.append(f"=== {channel} ===\n{posts_text}")
                all_results.append(f"=== {channel} ===\n{analysis}")

        if not all_results:
            return "", "Не удалось проанализировать каналы"

        return "\n\n".join(all_posts), "\n\n".join(all_results)

    # Алиас для обратной совместимости
    def _silent_analyze(self, user_id: int, channel: str):
        """Тихий анализ через Executor."""
        self._analyze_channel_via_executor(user_id, channel)

    # ==================== ИДЕИ ====================

    def propose_ideas(self, user_id: int) -> str:
        """
        Предложить идеи для постов на основе:
        1. Стиля и контекста пользователя
        2. Свежих топ-постов конкурентов
        3. Сохранённых трендов
        """
        context = self.build_smm_context(user_id)

        # Свежие топ-посты от конкурентов
        competitors = self.get_competitors(user_id)
        trending_posts = []
        if competitors:
            for channel in competitors[:3]:
                try:
                    posts = self.parser.get_top_posts(channel, limit=5)
                    for p in posts[:3]:
                        if not self._is_ad_post(p.text):
                            trending_posts.append(f"[{channel}] 👁{p.views}: {p.text[:120]}...")
                except Exception:
                    continue

        trending_text = "\n".join(trending_posts) if trending_posts else ""

        # Сохранённые тренды из памяти
        trends_items = self.db.fetch_all(
            """SELECT content FROM memory_items
               WHERE user_id = ? AND content LIKE 'Тренд:%'
               ORDER BY created_at DESC LIMIT 3""",
            (user_id,)
        )
        saved_trends = "\n".join([t[0] for t in trends_items]) if trends_items else ""

        # Формируем контент для анализа
        analysis_parts = []
        if trending_text:
            analysis_parts.append(f"СВЕЖИЕ ТОПЫ КОНКУРЕНТОВ:\n{trending_text}")
        if saved_trends:
            analysis_parts.append(f"ТРЕНДЫ ИЗ АНАЛИЗА:\n{saved_trends}")

        if not analysis_parts:
            return (
                "📭 Нет данных для генерации идей.\n\n"
                "Добавьте конкурентов: /competitor @channel\n"
                "Или запустите анализ: /analyze"
            )

        prompt = f"""На основе данных конкурентов предложи РОВНО 3 идеи для постов.

КОНТЕКСТ КЛИЕНТА:
{context}

{chr(10).join(analysis_parts)}

Для каждой идеи:
1. <b>Тема</b>: цепляющий заголовок
2. <b>Почему зайдёт</b>: 1 предложение
3. <b>Формат</b>: короткий/длинный, тон

ВАЖНО: Ровно 3 идеи, не больше и не меньше. Идеи должны быть РАЗНЫЕ по темам.
Выбирай темы которые УЖЕ работают у конкурентов."""

        response = self.llm.complete(
            messages=[
                Message.system("Ты SMM-эксперт. Анализируешь успешный контент и предлагаешь идеи."),
                Message.user(prompt)
            ],
            user_id=user_id
        )

        return _markdown_to_html(response.content)

    # ==================== ОТЧЁТЫ ====================

    def weekly_report(self, user_id: int) -> str:
        """Недельный отчёт."""
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        posts = self.db.fetch_all(
            """SELECT content FROM memory_items
               WHERE user_id = ? AND content LIKE 'Удачный пост:%'
               AND created_at > ?""",
            (user_id, week_ago)
        )

        posts_text = "\n".join([p[0] for p in posts]) if posts else "Нет постов за неделю"

        prompt = f"""Составь недельный отчёт по контенту.

ОПУБЛИКОВАННЫЕ ПОСТЫ:
{posts_text}

МЕТРИКИ:
Метрики недоступны

Напиши:
1. Что зашло лучше всего
2. Что не сработало
3. 2-3 рекомендации на следующую неделю

Кратко, конкретно, с цифрами."""

        response = self.llm.complete(
            messages=[
                Message.system("Ты аналитик контента."),
                Message.user(prompt)
            ],
            user_id=user_id
        )

        return _markdown_to_html(response.content)

    # ==================== НОВОСТИ И ПОИСК ====================

    def fetch_hot_news(self, user_id: int) -> tuple:
        """
        Получить горячие темы из источников ПОЛЬЗОВАТЕЛЯ:
        1. Добавленные RSS-источники
        2. Тренды из каналов конкурентов

        НЕ используем дефолтные tech-сайты!
        """
        content_parts = []

        # 1. RSS-источники пользователя
        user_sources = self.get_news_sources(user_id)
        if user_sources:
            news_items = []
            for src in user_sources[:5]:
                items = self.news.fetch_custom_rss(src["url"], src["name"], limit=3)
                news_items.extend(items)

            if news_items:
                news_text = []
                for n in news_items[:10]:
                    news_text.append(f"[{n.source}] {n.title}\n{n.summary[:150]}...")
                content_parts.append("НОВОСТИ ИЗ ВАШИХ ИСТОЧНИКОВ:\n" + "\n\n".join(news_text))

        # 2. Тренды из каналов конкурентов (топ посты)
        competitors = self.get_competitors(user_id)
        if competitors:
            trending_posts = []
            for channel in competitors[:3]:
                try:
                    posts = self.parser.get_top_posts(channel, limit=3)
                    for p in posts[:2]:
                        if not self._is_ad_post(p.text):
                            trending_posts.append(f"[{channel}] 👁{p.views}: {p.text[:150]}...")
                except Exception:
                    continue

            if trending_posts:
                content_parts.append("ПОПУЛЯРНОЕ У КОНКУРЕНТОВ:\n" + "\n\n".join(trending_posts))

        # Если ничего нет — подскажем что добавить
        if not content_parts:
            return "", (
                "📭 Нет источников для анализа.\n\n"
                "Добавьте:\n"
                "• Конкурентов: /competitor @channel\n"
                "• RSS-источники: /source"
            )

        raw_content = "\n\n---\n\n".join(content_parts)

        prompt = f"""Вот данные из источников пользователя:

{raw_content}

ЗАДАЧА: Предложи 3-5 идей для постов на основе этих данных.

Для каждой идеи:
1. <b>Тема поста</b>: цепляющий заголовок
2. <b>Суть</b>: о чём пост (1-2 предложения)
3. <b>Почему актуально</b>: почему это зайдёт аудитории

Выбирай самое интересное и хайповое. Пиши на русском."""

        response = self.llm.complete(
            messages=[
                Message.system("Ты SMM-эксперт. Анализируешь контент и предлагаешь идеи."),
                Message.user(prompt)
            ],
            user_id=user_id
        )

        ideas = _markdown_to_html(response.content)

        # Сохраняем в память
        self.memory.store(
            user_id=user_id,
            content=f"Горячие темы: {ideas[:400]}",
            memory_type=MemoryType.CONTEXT,
            importance=0.7,
            metadata={"source": "user_sources", "date": datetime.now().isoformat()}
        )

        return raw_content, ideas

    def search_for_post(self, user_id: int, query: str) -> str:
        """Поиск информации для поста."""
        print(f"[Web] Поиск: '{query}'")
        results = self.news.search_duckduckgo(query, limit=5)

        if not results:
            print(f"[Web] Ничего не найдено")
            return "Ничего не найдено"

        print(f"[Web] Найдено {len(results)} результатов")
        search_text = []
        for r in results:
            search_text.append(f"{r.title}\n{r.summary}")

        return "\n\n---\n\n".join(search_text)

    def generate_post_with_research(self, user_id: int, topic: str, style: str = None) -> PostDraft:
        """Сгенерировать пост с исследованием (всегда с web search)."""
        print(f"[Research] Тема требует актуальной инфы: '{topic}'")

        # Извлекаем канал из темы (если указан) — ищем и по словам в памяти
        target_channel = self._extract_channel_from_topic(topic, user_id=user_id)
        smm_context = self.build_smm_context(
            user_id,
            extra_style=style or "",
            target_channel=target_channel,
            topic=topic if not target_channel else None
        )

        # Создаём задачу с принудительным web search
        task = self.tasks.enqueue(
            user_id=user_id,
            task_type="smm_generate",
            input_text=topic,
            input_data={
                "user_id": user_id,
                "topic": topic,
                "smm_context": smm_context,
                "skip_web_search": False,  # Всегда искать
            }
        )

        # Напрямую переводим в running
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=300)

        self.db.execute(
            """UPDATE tasks
               SET status = 'running', locked_by = ?, locked_at = ?,
                   lease_expires_at = ?, started_at = ?, updated_at = ?
               WHERE id = ?""",
            ("smm_agent", now.isoformat(), lease_expires.isoformat(),
             now.isoformat(), now.isoformat(), task.id)
        )

        draft_text = ""
        try:
            running_task = self.tasks.get_task(task.id)
            if running_task:
                self.executor.run_task(running_task)
        except ApprovalRequired as e:
            draft_text = e.draft_content or ""

        if not draft_text:
            draft_text = self._get_draft_from_task(task.id)

        return PostDraft(
            text=draft_text,
            topic=topic,
            task_id=task.id,
            channel_id=self.get_channel_id(user_id) or ""
        )

    # ==================== РАСПИСАНИЕ ====================

    def get_pending_notifications(self, user_id: int) -> list:
        """Получить ожидающие уведомления для пользователя."""
        recent_trends = self.db.fetch_all(
            """SELECT id, content FROM memory_items
               WHERE user_id = ? AND content LIKE 'Тренд:%'
               AND created_at > datetime('now', '-1 hour')
               AND (metadata IS NULL OR metadata NOT LIKE '%"notified":true%')
               LIMIT 3""",
            (user_id,)
        )
        return recent_trends

    def mark_notified(self, memory_id: int):
        """Отметить что уведомление отправлено."""
        self.db.execute(
            "UPDATE memory_items SET metadata = json_set(COALESCE(metadata, '{}'), '$.notified', true) WHERE id = ?",
            (memory_id,)
        )

    def cleanup(self):
        """Очистить ресурсы."""
        if self._parser:
            self._parser.stop()
