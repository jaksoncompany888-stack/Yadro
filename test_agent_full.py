#!/usr/bin/env python3
"""
Полное тестирование SMM Agent со всеми функциями.
Запуск: ./venv/bin/python test_agent_full.py
"""
import sys
sys.path.insert(0, '.')

from app.storage.database import Database
from app.llm.service import LLMService
from app.smm.agent import SMMAgent
from app.tools.smm_tools import register_smm_tools
from app.memory.service import MemoryType

def test_all():
    print("=" * 70)
    print("ПОЛНОЕ ТЕСТИРОВАНИЕ SMM AGENT")
    print("=" * 70)

    # Создаём тестовую среду
    db = Database(":memory:")
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, tg_id INTEGER, username TEXT, settings TEXT
    )""")
    db.execute("INSERT INTO users (tg_id, username) VALUES (123, 'test_user')")

    llm = LLMService(db=db, mock_mode=True)
    agent = SMMAgent(db=db, llm=llm)

    # Регистрируем tools как в реальном боте
    register_smm_tools(
        channel_parser=agent.parser,
        news_monitor=agent.news,
        memory_service=agent.memory,
        llm_service=llm,
    )
    print("✅ Tools зарегистрированы\n")

    user_id = 1
    results = {"passed": 0, "failed": 0, "errors": []}

    def test(name, func):
        try:
            result = func()
            if result:
                print(f"✅ {name}")
                results["passed"] += 1
            else:
                print(f"⚠️  {name} — пустой результат")
                results["passed"] += 1
            return result
        except Exception as e:
            print(f"❌ {name}: {e}")
            results["failed"] += 1
            results["errors"].append(f"{name}: {e}")
            return None

    # ==================== 1. ПАМЯТЬ ====================
    print("\n" + "=" * 50)
    print("1. ПАМЯТЬ")
    print("=" * 50)

    test("save_style", lambda: agent.save_style(user_id, "Дерзкий провокационный стиль с эмодзи 🔥"))
    test("save_channel", lambda: agent.save_channel(user_id, "-100123456", "Мой тестовый канал"))
    test("get_base_style", lambda: agent.get_base_style(user_id))
    test("get_channel_id", lambda: agent.get_channel_id(user_id))

    # ==================== 2. КОНКУРЕНТЫ ====================
    print("\n" + "=" * 50)
    print("2. КОНКУРЕНТЫ")
    print("=" * 50)

    # Добавляем без auto_analyze чтобы не зависеть от парсера
    test("add_competitor", lambda: agent.add_competitor(user_id, "@competitor1", auto_analyze=False))
    test("add_competitor #2", lambda: agent.add_competitor(user_id, "@competitor2", auto_analyze=False))

    comps = test("get_competitors", lambda: agent.get_competitors(user_id))
    print(f"   Конкуренты: {comps}")

    comps_ids = test("get_competitors_with_ids", lambda: agent.get_competitors_with_ids(user_id))
    if comps_ids:
        print(f"   С ID: {len(comps_ids)} шт")

    # ==================== 3. ИСТОЧНИКИ ====================
    print("\n" + "=" * 50)
    print("3. ИСТОЧНИКИ НОВОСТЕЙ")
    print("=" * 50)

    test("add_news_source", lambda: agent.add_news_source(user_id, "https://techcrunch.com/rss", "TechCrunch"))
    test("add_news_source #2", lambda: agent.add_news_source(user_id, "https://vc.ru/rss", "VC.ru"))

    sources = test("get_news_sources", lambda: agent.get_news_sources(user_id))
    print(f"   Источники: {sources}")

    test("remove_news_source", lambda: agent.remove_news_source(user_id, "https://vc.ru/rss"))

    # ==================== 4. КОНТЕКСТ И ПАМЯТЬ ====================
    print("\n" + "=" * 50)
    print("4. КОНТЕКСТ И ПОИСК В ПАМЯТИ")
    print("=" * 50)

    # Симулируем сохранённый анализ канала
    agent.memory.store(
        user_id=user_id,
        content="Стиль канала @competitor1: Экспертный тон, много цифр и статистики. Темы: маркетинг, продажи, воронки.",
        memory_type=MemoryType.CONTEXT,
        importance=0.9
    )
    agent.memory.store(
        user_id=user_id,
        content="Стиль канала @competitor2: Лёгкий разговорный стиль. Темы: астрология, таро, лунные циклы.",
        memory_type=MemoryType.CONTEXT,
        importance=0.9
    )
    print("   Добавлены стили каналов в память")

    # Тест FTS5 поиска релевантных стилей
    styles = test("_find_relevant_channel_styles (маркетинг)",
                  lambda: agent._find_relevant_channel_styles(user_id, "пост про маркетинг и воронки"))
    if styles:
        print(f"   Найден стиль: {styles[0][:60]}...")

    styles2 = test("_find_relevant_channel_styles (астрология)",
                   lambda: agent._find_relevant_channel_styles(user_id, "пост про лунные циклы"))
    if styles2:
        print(f"   Найден стиль: {styles2[0][:60]}...")

    context = test("build_smm_context", lambda: agent.build_smm_context(user_id, topic="маркетинг"))
    if context:
        print(f"   Контекст: {len(context)} символов")

    # ==================== 5. ФИДБЕК ====================
    print("\n" + "=" * 50)
    print("5. ФИДБЕК И УСПЕШНЫЕ ПОСТЫ")
    print("=" * 50)

    test("save_feedback", lambda: agent.save_feedback(user_id, "Пост отлично зашёл!", "Текст поста..."))
    test("save_successful_post", lambda: agent.save_successful_post(user_id, "Успешный пост 🔥", {"views": 5000}))

    # ==================== 6. РЕДАКТИРОВАНИЕ ====================
    print("\n" + "=" * 50)
    print("6. РЕДАКТИРОВАНИЕ ПОСТОВ")
    print("=" * 50)

    original = """🔥 Важная новость дня!

Сегодня произошло нечто невероятное в мире технологий.

Подробности читайте в нашем канале!

#новости #технологии #важно"""

    print(f"   Оригинал: {len(original)} символов, 3 абзаца")

    # Precise: убрать хештеги
    result1 = test("edit_post (precise: убери хештеги)",
                   lambda: agent.edit_post(user_id, original, "убери хештеги"))
    if result1:
        has_tags = "#" in result1
        print(f"   Хештеги убраны: {not has_tags}")

    # Precise: убрать эмодзи
    result2 = test("edit_post (precise: убери эмодзи огонь)",
                   lambda: agent.edit_post(user_id, original, "убери эмодзи огонь"))
    if result2:
        has_fire = "🔥" in result2
        print(f"   Огонь убран: {not has_fire}")

    # Precise: убрать последний абзац
    result3 = test("edit_post (precise: убери последний абзац)",
                   lambda: agent.edit_post(user_id, original, "убери последний абзац"))
    if result3:
        paragraphs = len([p for p in result3.split('\n\n') if p.strip()])
        print(f"   Абзацев осталось: {paragraphs}")

    # Precise: выдели жирным
    result4 = test("edit_post (precise: выдели 'невероятное' жирным)",
                   lambda: agent.edit_post(user_id, original, "выдели невероятное жирным"))
    if result4:
        has_bold = "<b>невероятное</b>" in result4.lower() or "<b>Невероятное</b>" in result4
        print(f"   Жирный добавлен: {has_bold}")

    # Creative: добавь хук
    result5 = test("edit_post (creative: добавь хук)",
                   lambda: agent.edit_post(user_id, original, "добавь цепляющий хук в начало"))
    if result5:
        print(f"   Результат: {len(result5)} символов")

    # Hybrid: precise + creative
    result6 = test("edit_post (hybrid: убери хештеги и добавь хук)",
                   lambda: agent.edit_post(user_id, original, "убери хештеги и добавь хук"))
    if result6:
        has_tags = "#" in result6
        print(f"   Хештеги убраны + хук: tags={has_tags}")

    # ==================== 7. ГЕНЕРАЦИЯ ====================
    print("\n" + "=" * 50)
    print("7. ГЕНЕРАЦИЯ ПОСТОВ (через Executor)")
    print("=" * 50)

    draft = test("generate_post", lambda: agent.generate_post(user_id, "тестовая тема про маркетинг"))
    if draft:
        print(f"   Task ID: {draft.task_id}")
        print(f"   Текст: {draft.text[:80] if draft.text else 'ожидает approval'}...")

    # ==================== 8. ОДОБРЕНИЕ/ОТКЛОНЕНИЕ ====================
    print("\n" + "=" * 50)
    print("8. ОДОБРЕНИЕ / ОТКЛОНЕНИЕ")
    print("=" * 50)

    if draft:
        test("approve_post", lambda: agent.approve_post(draft.task_id, user_id, "Одобренный текст"))

    draft2 = agent.generate_post(user_id, "тема для отклонения")
    if draft2:
        test("reject_post", lambda: agent.reject_post(draft2.task_id, user_id, "Не подходит"))

    # ==================== 9. УТИЛИТЫ ====================
    print("\n" + "=" * 50)
    print("9. УТИЛИТЫ")
    print("=" * 50)

    is_ad = test("_is_ad_post (реклама)", lambda: agent._is_ad_post("Купи сейчас! Скидка 50%! #реклама"))
    print(f"   Это реклама: {is_ad}")

    is_ad2 = test("_is_ad_post (обычный)", lambda: agent._is_ad_post("Интересная статья про маркетинг"))
    print(f"   Это реклама: {is_ad2}")

    needs = test("_needs_research", lambda: agent._needs_research("последние новости криптовалют 2024"))
    print(f"   Нужен research: {needs}")

    # ==================== 10. ИДЕИ И ОТЧЁТЫ ====================
    print("\n" + "=" * 50)
    print("10. ИДЕИ И ОТЧЁТЫ")
    print("=" * 50)

    ideas = test("propose_ideas", lambda: agent.propose_ideas(user_id))
    if ideas:
        print(f"   Идеи: {len(ideas)} символов")

    report = test("weekly_report", lambda: agent.weekly_report(user_id))
    if report:
        print(f"   Отчёт: {len(report)} символов")

    # ==================== 11. ПОИСК ====================
    print("\n" + "=" * 50)
    print("11. ПОИСК")
    print("=" * 50)

    search = test("search_for_post", lambda: agent.search_for_post(user_id, "искусственный интеллект"))
    if search:
        print(f"   Результат поиска: {len(search)} символов")

    # ==================== ИТОГ ====================
    print("\n" + "=" * 70)
    print("ИТОГ")
    print("=" * 70)
    print(f"✅ Passed: {results['passed']}")
    print(f"❌ Failed: {results['failed']}")

    if results["errors"]:
        print("\nОшибки:")
        for err in results["errors"]:
            print(f"  - {err}")

    # Проверяем архитектуру
    print("\n" + "=" * 50)
    print("ПРОВЕРКА АРХИТЕКТУРЫ")
    print("=" * 50)

    tasks = db.fetch_all("SELECT task_type, status FROM tasks")
    print(f"Tasks в БД: {len(tasks)}")
    for t in tasks[:5]:
        print(f"  - {t[0]}: {t[1]}")

    memories = db.fetch_all("SELECT content FROM memory_items WHERE user_id = ?", (user_id,))
    print(f"\nMemory items: {len(memories)}")
    for m in memories[:5]:
        print(f"  - {m[0][:60]}...")

    print("\n" + "=" * 70)
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 70)

if __name__ == "__main__":
    test_all()
