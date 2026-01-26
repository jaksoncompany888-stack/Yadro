"""
Фоновые задачи SMM агента
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from aiogram.enums import ParseMode

from app.storage import Database
from app.llm import LLMService
from app.smm.agent import SMMAgent


class SMMScheduler:
    """Фоновый планировщик для SMM агента"""

    def __init__(self, db: Database, llm: LLMService, bot, check_interval: int = 60):
        """
        check_interval: интервал проверки в секундах (по умолчанию 60 сек для отложенных постов)
        """
        self.db = db
        self.llm = llm
        self.bot = bot
        self.agent = SMMAgent(db=db, llm=llm)
        self.check_interval = check_interval
        self._running = False

    async def start(self):
        """Запустить фоновый мониторинг"""
        self._running = True
        print(f"[Scheduler] Запущен. Интервал: {self.check_interval} сек")

        # При старте — очистить застрявшие задачи
        self._cleanup_stuck_tasks()

        # При старте — переанализировать устаревшие каналы
        await self._reanalyze_outdated_channels()

        # Запускаем основной цикл
        await self._run_loop()

    def _cleanup_stuck_tasks(self):
        """
        Очистить застрявшие задачи при старте бота.

        Если бот перезапустился — старые running/queued задачи уже не выполняются.
        """
        # Отменяем running задачи (они застряли после перезапуска)
        cancelled_running = self.db.execute(
            """UPDATE tasks SET status = 'cancelled',
               error = 'Отменено при перезапуске бота'
               WHERE status = 'running'"""
        )

        # Отменяем queued задачи старше 1 часа
        cancelled_queued = self.db.execute(
            """UPDATE tasks SET status = 'cancelled',
               error = 'Отменено: слишком долго в очереди'
               WHERE status = 'queued'
               AND created_at < datetime('now', '-1 hour')"""
        )

        # Отменяем paused задачи старше 24 часов (пользователь забыл)
        cancelled_paused = self.db.execute(
            """UPDATE tasks SET status = 'cancelled',
               error = 'Отменено: истёк срок ожидания'
               WHERE status = 'paused'
               AND updated_at < datetime('now', '-24 hours')"""
        )

        # Считаем сколько отменили
        total = self.db.fetch_value(
            "SELECT COUNT(*) FROM tasks WHERE status = 'cancelled' AND error LIKE 'Отменено%'",
            default=0
        )

        if total > 0:
            print(f"[Scheduler] Очищено застрявших задач: {total}")

    async def _run_loop(self):
        """Основной цикл планировщика"""
        while self._running:
            try:
                await self._run_scheduled_tasks()
            except Exception as e:
                print(f"[Scheduler] Ошибка: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self):
        """Остановить"""
        self._running = False

    async def _run_scheduled_tasks(self):
        """Выполнить запланированные задачи"""
        now = datetime.now()
        hour = now.hour

        # Публикация отложенных постов (каждую проверку)
        await self._publish_scheduled_drafts()

        # Ночной скан каналов (3:00 - 4:00) — раз в 3 дня
        if 3 <= hour < 4:
            await self._channels_background_scan()

        # TODO: Переделать на идеи из каналов пользователя + его источников
        # Пока отключено — слишком навязчиво и из неправильных источников
        # # Утренний мониторинг новостей (9:00 - 10:00)
        # if 9 <= hour < 10:
        #     await self._morning_news_scan()
        #
        # # Вечерние идеи (19:00 - 20:00)
        # if 19 <= hour < 20:
        #     await self._evening_ideas()

        # Воскресный отчёт (12:00 - 13:00)
        if now.weekday() == 6 and 12 <= hour < 13:
            await self._weekly_report()

    async def _publish_scheduled_drafts(self):
        """Публикация отложенных постов"""
        now = datetime.now().isoformat()

        # Находим посты, которые пора публиковать
        drafts = self.db.fetch_all(
            """SELECT d.id, d.text, d.channel_id, d.user_id, u.tg_id
               FROM drafts d
               JOIN users u ON d.user_id = u.id
               WHERE d.status = 'scheduled' AND d.publish_at <= ?""",
            (now,)
        )

        for draft_id, text, channel_id, user_id, tg_id in drafts:
            try:
                # Публикуем с HTML форматированием
                try:
                    await self.bot.send_message(channel_id, text, parse_mode=ParseMode.HTML)
                except Exception:
                    await self.bot.send_message(channel_id, text)

                # Обновляем статус
                self.db.execute(
                    "UPDATE drafts SET status = 'published' WHERE id = ?",
                    (draft_id,)
                )

                # Сохраняем как успешный пост для обучения стилю
                self.agent.memory.store_decision(user_id, f"Опубликованный пост:\n{text}")

                # Уведомляем пользователя
                if tg_id:
                    await self.bot.send_message(
                        tg_id,
                        f"✅ Опубликовано по расписанию:\n\n{text[:200]}...",
                        parse_mode=None
                    )

                print(f"[Scheduler] Опубликован отложенный пост {draft_id}")

            except Exception as e:
                print(f"[Scheduler] Ошибка публикации поста {draft_id}: {e}")
                # Помечаем как ошибку
                self.db.execute(
                    "UPDATE drafts SET status = 'error' WHERE id = ?",
                    (draft_id,)
                )

    async def _morning_news_scan(self):
        """Утренний скан новостей для всех активных пользователей"""
        users = self.db.fetch_all(
            "SELECT DISTINCT user_id FROM memory_items WHERE content LIKE 'Канал:%'"
        )

        for (user_id,) in users:
            # Проверяем не сканировали ли уже сегодня
            today_scan = self.db.fetch_value(
                """SELECT id FROM memory_items
                   WHERE user_id = ? AND content LIKE 'Горячие темы:%'
                   AND created_at > datetime('now', '-12 hours')""",
                (user_id,)
            )

            if today_scan:
                continue

            try:
                raw_news, ideas = self.agent.fetch_hot_news(user_id)

                if ideas and "Не удалось" not in ideas:
                    # Получаем tg_id
                    tg_id = self.db.fetch_value(
                        "SELECT tg_id FROM users WHERE id = ?", (user_id,)
                    )

                    if tg_id:
                        await self.bot.send_message(
                            tg_id,
                            f"🔥 Утренний дайджест горячих тем:\n\n{ideas[:3500]}",
                            parse_mode=None
                        )
                        print(f"[Scheduler] Отправлен дайджест пользователю {user_id}")
            except Exception as e:
                print(f"[Scheduler] Ошибка сканирования для {user_id}: {e}")

    async def _evening_ideas(self):
        """Вечерние идеи для постов"""
        users = self.db.fetch_all(
            "SELECT DISTINCT user_id FROM memory_items WHERE content LIKE 'Канал:%'"
        )

        for (user_id,) in users:
            # Проверяем не отправляли ли уже
            today_ideas = self.db.fetch_value(
                """SELECT id FROM memory_items
                   WHERE user_id = ? AND content LIKE 'Тренд:%'
                   AND created_at > datetime('now', '-8 hours')""",
                (user_id,)
            )

            if today_ideas:
                continue

            try:
                ideas = self.agent.propose_ideas(user_id)

                if ideas:
                    tg_id = self.db.fetch_value(
                        "SELECT tg_id FROM users WHERE id = ?", (user_id,)
                    )

                    if tg_id:
                        await self.bot.send_message(
                            tg_id,
                            f"💡 Идеи для постов на завтра:\n\n{ideas[:3500]}",
                            parse_mode=None
                        )
                        print(f"[Scheduler] Отправлены идеи пользователю {user_id}")
            except Exception as e:
                print(f"[Scheduler] Ошибка идей для {user_id}: {e}")

    async def _weekly_report(self):
        """Воскресный недельный отчёт"""
        users = self.db.fetch_all(
            "SELECT DISTINCT user_id FROM memory_items WHERE content LIKE 'Канал:%'"
        )

        for (user_id,) in users:
            # Проверяем не отправляли ли уже
            this_week = self.db.fetch_value(
                """SELECT id FROM memory_items
                   WHERE user_id = ? AND content LIKE 'Отчёт:%'
                   AND created_at > datetime('now', '-6 days')""",
                (user_id,)
            )

            if this_week:
                continue

            try:
                report = self.agent.weekly_report(user_id)

                if report:
                    # Сохраняем отчёт
                    self.agent.memory.store_decision(
                        user_id, f"Отчёт: {report[:200]}..."
                    )

                    tg_id = self.db.fetch_value(
                        "SELECT tg_id FROM users WHERE id = ?", (user_id,)
                    )

                    if tg_id:
                        await self.bot.send_message(
                            tg_id,
                            f"📊 Недельный отчёт:\n\n{report[:3500]}",
                            parse_mode=None
                        )
                        print(f"[Scheduler] Отправлен отчёт пользователю {user_id}")
            except Exception as e:
                print(f"[Scheduler] Ошибка отчёта для {user_id}: {e}")

    async def _channels_background_scan(self):
        """Фоновый скан каналов раз в 3 дня — тихо, без уведомлений"""
        # Защита от повторного запуска в течение суток
        if hasattr(self, '_last_background_scan'):
            elapsed = (datetime.now() - self._last_background_scan).total_seconds()
            if elapsed < 86400:  # 24 часа
                return
        self._last_background_scan = datetime.now()

        three_days_ago = (datetime.now() - timedelta(days=3)).isoformat()
        CURRENT_VERSION = "v2"

        # Все каналы которые не анализировали 3+ дней ИЛИ с устаревшей версией
        channels = self.db.fetch_all(
            """SELECT DISTINCT m.user_id, m.content
               FROM memory_items m
               WHERE m.content LIKE 'Конкурент:%'
               AND NOT EXISTS (
                   SELECT 1 FROM memory_items m2
                   WHERE m2.user_id = m.user_id
                   AND m2.content LIKE 'Стиль канала%' || SUBSTR(m.content, 12) || '%'
                   AND m2.created_at > ?
                   AND m2.metadata LIKE '%"analysis_version":"' || ? || '"%'
               )""",
            (three_days_ago, CURRENT_VERSION)
        )

        if not channels:
            return

        print(f"[Scheduler] Фоновый скан: {len(channels)} каналов")

        for user_id, content in channels:
            channel = content.replace("Конкурент:", "").strip()

            try:
                # 1. Парсим бесплатно — только проверяем есть ли новые посты
                posts = self.agent.parser.get_recent_posts(channel, limit=5)

                if not posts:
                    continue

                # 2. Есть новые посты — анализируем через LLM
                success = self.agent._analyze_channel_via_executor(user_id, channel)
                if success:
                    print(f"[Scheduler] Обновлён анализ {channel} для user {user_id}")
                else:
                    # Ошибка (возможно лимит) — прекращаем скан
                    print(f"[Scheduler] Прерываем скан из-за ошибки")
                    break

                # Пауза между каналами чтобы не спамить
                await asyncio.sleep(2)

            except Exception as e:
                print(f"[Scheduler] Ошибка скана {channel}: {e}")
                # При любой ошибке прекращаем скан
                break

    async def _reanalyze_outdated_channels(self):
        """
        Переанализировать каналы с устаревшей схемой анализа.
        Вызывается при старте бота.

        Версии анализа:
        - v1 (или без версии): старый анализ без глубоких метрик
        - v2: глубокий анализ с compute_channel_metrics
        """
        CURRENT_VERSION = "v2"

        # Находим всех конкурентов
        competitors = self.db.fetch_all(
            """SELECT DISTINCT m.user_id, m.content
               FROM memory_items m
               WHERE m.content LIKE 'Конкурент:%'"""
        )

        if not competitors:
            return

        print(f"[Scheduler] Проверка {len(competitors)} каналов на устаревший анализ...")

        outdated_count = 0

        for user_id, content in competitors:
            channel = content.replace("Конкурент:", "").strip()

            # Проверяем есть ли анализ с текущей версией
            analysis = self.db.fetch_one(
                """SELECT id, metadata FROM memory_items
                   WHERE user_id = ? AND content LIKE ?
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, f"Стиль канала {channel}%")
            )

            needs_reanalysis = False

            if not analysis:
                # Нет анализа вообще
                needs_reanalysis = True
                print(f"[Scheduler] {channel}: нет анализа")
            else:
                # Есть анализ — проверяем версию
                import json
                try:
                    metadata = json.loads(analysis[1]) if analysis[1] else {}
                    version = metadata.get("analysis_version", "v1")
                    if version != CURRENT_VERSION:
                        needs_reanalysis = True
                        print(f"[Scheduler] {channel}: устаревшая версия {version}")
                except:
                    needs_reanalysis = True
                    print(f"[Scheduler] {channel}: битый metadata")

            if needs_reanalysis:
                outdated_count += 1
                try:
                    # Переанализируем через Executor
                    success = self.agent._analyze_channel_via_executor(user_id, channel)
                    if success:
                        print(f"[Scheduler] ✓ Переанализирован {channel}")
                    else:
                        # Ошибка (лимит или другое) — прекращаем переанализ
                        print(f"[Scheduler] ✗ Прерываем переанализ из-за ошибки")
                        break
                    await asyncio.sleep(1)  # Пауза между каналами
                except Exception as e:
                    print(f"[Scheduler] ✗ Ошибка переанализа {channel}: {e}")
                    break  # При любой ошибке прекращаем

        if outdated_count > 0:
            print(f"[Scheduler] Переанализировано {outdated_count} каналов")
        else:
            print(f"[Scheduler] Все каналы актуальны (версия {CURRENT_VERSION})")
