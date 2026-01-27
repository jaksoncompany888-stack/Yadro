"""
SMM Agent Bot - полная версия с голосом
"""
import os
import asyncio
import tempfile
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

load_dotenv()

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.storage import Database
from app.llm import LLMService
from app.smm.agent import SMMAgent
from app.smm.scheduler_tasks import SMMScheduler
from app.kernel.task_manager import TaskLimitError

# Config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # URL для Mini App (например: https://your-domain.com)

# Init
db = Database("data/smm_agent.db")
llm = LLMService(db=db, mock_mode=False, openai_api_key=OPENAI_KEY)
agent = SMMAgent(db=db, llm=llm)

# Регистрация SMM tools в ToolRegistry
from app.tools.smm_tools import register_smm_tools
register_smm_tools(
    channel_parser=agent.parser,
    news_monitor=agent.news,
    memory_service=agent.memory,
    llm_service=llm,
)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# Состояния
user_states = {}  # {tg_id: {"state": "...", "data": {...}}}
pending_posts = {}  # {tg_id: PostDraft}

# Главное меню — МИНИМУМ
def get_main_menu():
    """Создаёт главное меню с учётом наличия Mini App."""
    keyboard = [
        [KeyboardButton(text="🎤 Создать пост")],
        [KeyboardButton(text="💡 Идеи на сегодня"), KeyboardButton(text="📋 Черновики")],
        [KeyboardButton(text="⚙️")]
    ]
    # Добавляем кнопку календаря если настроен WEBAPP_URL
    if WEBAPP_URL:
        keyboard.insert(0, [KeyboardButton(text="📅 Календарь", web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

main_menu = get_main_menu()

# Настройки — убрали Стиль и Источники (пока не готовы)
settings_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📺 Сменить канал"), KeyboardButton(text="👥 Конкуренты")],
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)


def get_user_id(tg_id: int) -> int:
    """Получить или создать user в БД"""
    existing = db.fetch_value("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
    if existing:
        return existing
    return db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, f"user_{tg_id}"))


async def show_main_screen(message: Message):
    """Главный экран — минимум кнопок"""
    await message.answer(
        "Готов к работе. Скажи тему поста голосом или текстом.",
        parse_mode=None,
        reply_markup=main_menu
    )


async def send_post(message: Message, text: str, reply_markup=None):
    """Отправить пост с HTML-форматированием"""
    try:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        # Если HTML сломан — отправляем без форматирования
        await message.answer(text, parse_mode=None, reply_markup=reply_markup)


def _markdown_to_html(text: str) -> str:
    """Конвертация markdown в HTML."""
    import re
    # **text** → <b>text</b>
    text = re.sub(r'\*\*([^\*]+?)\*\*', r'<b>\1</b>', text)
    # _text_ → <i>text</i>
    text = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)', r'<i>\1</i>', text)
    # Убираем дубли тегов
    while '<b><b>' in text:
        text = text.replace('<b><b>', '<b>')
    while '</b></b>' in text:
        text = text.replace('</b></b>', '</b>')
    return text


def _restore_formatting(original: str, edited: str) -> str:
    """
    LLM восстанавливает форматирование из оригинала.

    Только то что было жирным в оригинале — делает жирным в новом тексте.
    """
    import re
    import json

    # Находим что было жирным в оригинале
    bold_patterns = re.findall(r'<b>(.*?)</b>', original)
    if not bold_patterns:
        return edited  # Нечего восстанавливать

    prompt = f"""ОРИГИНАЛ (с форматированием):
{original}

НОВЫЙ ТЕКСТ (без форматирования):
{edited}

В оригинале жирным было: {bold_patterns}

Восстанови форматирование в новом тексте:
- Если текст из оригинала остался — сделай его жирным тегом <b>
- Если текст изменился но смысл тот же (например 15% → 20%) — тоже сделай жирным
- НЕ добавляй жирный там где его не было в оригинале

Верни ТОЛЬКО готовый текст с тегами <b></b>, без пояснений."""

    from app.smm.bot import llm
    response = llm.complete_simple(prompt)

    result = response.strip()
    # Убираем markdown обёртки если есть
    if result.startswith("```"):
        result = re.sub(r'^```\w*\s*', '', result)
        result = re.sub(r'\s*```$', '', result)

    # Проверка адекватности
    if len(result) < len(edited) * 0.5 or len(result) > len(edited) * 2:
        return edited  # LLM вернул мусор

    return result


def post_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для готового поста"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{task_id}"),
            InlineKeyboardButton(text="📋 В черновики", callback_data=f"draft_{task_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data="edit"),
            InlineKeyboardButton(text="📝 Вручную", callback_data="manual_edit"),
            InlineKeyboardButton(text="🔄 Заново", callback_data="regen")
        ]
    ])


def edit_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для режима редактирования"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="↩️ Откатить", callback_data="rollback"),
            InlineKeyboardButton(text="✏️ Ещё правка", callback_data="edit"),
            InlineKeyboardButton(text="📝 Вручную", callback_data="manual_edit")
        ],
        [
            InlineKeyboardButton(text="✅ Готово", callback_data=f"pub_{task_id}"),
            InlineKeyboardButton(text="📋 В черновики", callback_data=f"draft_{task_id}")
        ]
    ])


def calendar_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой открытия календаря (Mini App)"""
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Открыть календарь", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])


async def transcribe_voice(voice_file_id: str) -> str:
    """Транскрибировать голосовое сообщение"""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_KEY)

    file = await bot.get_file(voice_file_id)
    file_bytes = await bot.download_file(file.file_path)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(file_bytes.read())
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as audio:
            response = client.audio.transcriptions.create(model="whisper-1", file=audio, language="ru")
        return response.text
    finally:
        os.unlink(tmp_path)


# ==================== КОМАНДЫ ====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = get_user_id(message.from_user.id)
    channel = agent.get_channel_id(user_id)

    # Всегда показываем полную инструкцию
    instruction = (
        "Привет! Я SMM-агент, который пишет посты в твоём стиле.\n\n"
        "<b>🎯 Главные кнопки:</b>\n"
        "• <b>🎤 Создать пост</b> — напиши тему или голосовое\n"
        "• <b>💡 Идеи на сегодня</b> — 3 идеи для постов\n"
        "• <b>📋 Черновики</b> — сохранённые посты\n"
        "• <b>📅 Календарь</b> — (в разработке)\n"
        "• <b>⚙️</b> — настройки\n\n"
        "<b>💬 Когда создаёшь пост:</b>\n"
        "• ✅ Опубликовать — сразу в канал\n"
        "• 📋 В черновики — сохранить на потом\n"
        "• ✏️ Изменить — скажи что поправить\n"
        "• 🔄 Заново — другой вариант\n\n"
    )

    if not channel:
        # Нет канала — просим подключить
        user_states[message.from_user.id] = {"state": "onboarding_channel"}
        await message.answer(
            instruction + "<b>Начнём:</b>\nНапиши @username твоего канала",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        # Канал есть — показываем меню
        await message.answer(
            instruction + "Напиши тему поста или нажми кнопку.",
            parse_mode="HTML",
            reply_markup=main_menu
        )


@dp.message(Command("channel"))
async def cmd_channel(message: Message):
    user_states[message.from_user.id] = {"state": "set_channel"}
    await message.answer("Перешли сообщение из канала или отправь @username")


@dp.message(Command("style"))
async def cmd_style(message: Message):
    user_states[message.from_user.id] = {"state": "onboarding_style"}
    await message.answer("Опиши новый стиль постов (текстом или голосом):")


@dp.message(Command("post"))
async def cmd_post(message: Message):
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)

    if not agent.get_channel_id(user_id):
        await message.answer("Сначала подключи канал: /channel")
        return

    user_states[tg_id] = {"state": "post_topic"}
    await message.answer("О чём пост? (текстом или голосом)")


@dp.message(Command("competitor"))
async def cmd_competitor(message: Message):
    user_states[message.from_user.id] = {"state": "add_competitor"}
    await message.answer("Отправь @username канала-конкурента", parse_mode=None)


@dp.message(Command("competitors"))
async def cmd_list_competitors(message: Message):
    user_id = get_user_id(message.from_user.id)
    competitors = agent.get_competitors_with_ids(user_id)

    buttons = []

    # Кнопка добавления всегда сверху
    buttons.append([InlineKeyboardButton(text="➕ Добавить конкурента", callback_data="add_competitor")])

    if competitors:
        text = "Твои конкуренты:\n\nНажми на канал для действий:"
        for i, c in enumerate(competitors, 1):
            text += f"\n{i}. {c['channel']}"
            # При нажатии — меню с опциями
            buttons.append([InlineKeyboardButton(
                text=f"{c['channel']}",
                callback_data=f"comp_menu_{c['id']}_{c['channel']}"
            )])

        buttons.append([InlineKeyboardButton(text="🗑 Удалить всех", callback_data="clear_comps")])
    else:
        text = "Нет конкурентов.\n\nДобавь каналы, откуда черпаешь идеи — я буду анализировать их топовые посты."

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode=None)


@dp.message(Command("clear_competitors"))
async def cmd_clear_competitors(message: Message):
    user_id = get_user_id(message.from_user.id)
    db.execute("DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Конкурент:%'", (user_id,))
    await message.answer("Список конкурентов очищен", parse_mode=None)


@dp.callback_query(F.data == "add_competitor")
async def cb_add_competitor(callback: CallbackQuery):
    """Добавить конкурента через кнопку"""
    tg_id = callback.from_user.id
    user_states[tg_id] = {"state": "add_competitor"}

    await callback.message.answer("Напиши @username канала-конкурента", parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data == "add_source")
async def cb_add_source(callback: CallbackQuery):
    """Добавить источник через кнопку"""
    tg_id = callback.from_user.id
    user_states[tg_id] = {"state": "add_source"}

    await callback.message.answer(
        "Отправь ссылку на RSS.\n\n"
        "Примеры:\n"
        "• https://psychcentral.com/feed\n"
        "• https://techcrunch.com/feed/",
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data == "clear_sources")
async def cb_clear_sources(callback: CallbackQuery):
    """Удалить все источники"""
    user_id = get_user_id(callback.from_user.id)
    db.execute("DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Источник:%'", (user_id,))
    await callback.message.edit_text("Источники удалены", parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data.startswith("comp_menu_"))
async def cb_competitor_menu(callback: CallbackQuery):
    """Меню действий для конкурента"""
    # Формат: comp_menu_{id}_{channel}
    parts = callback.data.replace("comp_menu_", "").split("_", 1)
    memory_id = int(parts[0])
    channel = parts[1] if len(parts) > 1 else "канал"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Анализировать", callback_data=f"analyze_comp_{memory_id}_{channel}")],
        [InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_comp_{memory_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_competitors")]
    ])

    await callback.message.edit_text(
        f"Канал: {channel}\n\nВыбери действие:",
        reply_markup=keyboard,
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("analyze_comp_"))
async def cb_analyze_competitor(callback: CallbackQuery):
    """Глубокий анализ конкурента"""
    # Формат: analyze_comp_{id}_{channel}
    parts = callback.data.replace("analyze_comp_", "").split("_", 1)
    channel = parts[1] if len(parts) > 1 else ""
    user_id = get_user_id(callback.from_user.id)

    await callback.message.edit_text(f"Анализирую {channel}...\n\nЭто займёт немного времени.", parse_mode=None)
    await callback.answer()

    try:
        raw_posts, analysis = agent.analyze_single_channel(user_id, channel)

        if raw_posts:
            # Кнопка "Написать пост в этом стиле"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Написать пост в этом стиле", callback_data=f"write_style_{channel}")],
                [InlineKeyboardButton(text="🔙 К конкурентам", callback_data="back_to_competitors")]
            ])
            await send_post(callback.message, f"📊 АНАЛИЗ {channel}:\n\n{analysis}", reply_markup=keyboard)
        else:
            await send_post(callback.message, analysis)
    except Exception as e:
        await callback.message.answer(f"Ошибка: {e}", parse_mode=None)


@dp.callback_query(F.data.startswith("write_style_"))
async def cb_write_in_style(callback: CallbackQuery):
    """Написать пост в стиле конкретного канала"""
    channel = callback.data.replace("write_style_", "")
    tg_id = callback.from_user.id

    # Сохраняем канал в user_states
    user_states[tg_id] = {"state": "post_topic_styled", "target_channel": channel}

    await callback.message.answer(
        f"✍️ Пишем пост в стиле {channel}\n\nО чём будет пост? Напиши тему:",
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_competitors")
async def cb_back_to_competitors(callback: CallbackQuery):
    """Вернуться к списку конкурентов"""
    user_id = get_user_id(callback.from_user.id)
    competitors = agent.get_competitors_with_ids(user_id)

    buttons = []
    buttons.append([InlineKeyboardButton(text="➕ Добавить конкурента", callback_data="add_competitor")])

    if competitors:
        text = "Твои конкуренты:\n\nНажми на канал для действий:"
        for i, c in enumerate(competitors, 1):
            text += f"\n{i}. {c['channel']}"
            buttons.append([InlineKeyboardButton(
                text=f"{c['channel']}",
                callback_data=f"comp_menu_{c['id']}_{c['channel']}"
            )])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить всех", callback_data="clear_comps")])
    else:
        text = "Нет конкурентов."

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data.startswith("del_comp_"))
async def cb_delete_competitor(callback: CallbackQuery):
    memory_id = int(callback.data.replace("del_comp_", ""))
    agent.remove_competitor(memory_id)
    await callback.message.edit_text("Конкурент удалён", parse_mode=None)
    await callback.answer("Удалено")


@dp.callback_query(F.data == "clear_comps")
async def cb_clear_all_competitors(callback: CallbackQuery):
    user_id = get_user_id(callback.from_user.id)
    db.execute("DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Конкурент:%'", (user_id,))
    await callback.message.edit_text("Все конкуренты удалены", parse_mode=None)
    await callback.answer("Очищено")


@dp.message(Command("analyze"))
async def cmd_analyze(message: Message):
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)

    competitors = agent.get_competitors_with_ids(user_id)

    if not competitors:
        await message.answer("Нет конкурентов. Добавь: /competitor @channel", parse_mode=None)
        return

    if len(competitors) == 1:
        # Если один — сразу анализируем
        await _analyze_single(message, user_id, competitors[0]['channel'])
    else:
        # Если несколько — спрашиваем какой
        buttons = []
        for c in competitors:
            buttons.append([InlineKeyboardButton(
                text=c['channel'],
                callback_data=f"analyze_{c['channel']}"
            )])
        buttons.append([InlineKeyboardButton(text="📊 Все сразу", callback_data="analyze_all")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer("Какой канал анализировать?", reply_markup=keyboard, parse_mode=None)


async def _analyze_single(message: Message, user_id: int, channel: str):
    """Анализ одного канала"""
    await message.answer(f"Анализирую {channel}...", parse_mode=None)

    try:
        raw_posts, analysis = agent.analyze_single_channel(user_id, channel)

        if raw_posts:
            await message.answer(f"ПОСТЫ {channel}:\n\n{raw_posts[:3000]}", parse_mode=None)
            await send_post(message, f"АНАЛИЗ:\n\n{analysis}")
        else:
            await send_post(message, analysis)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", parse_mode=None)
    finally:
        agent.cleanup()


@dp.callback_query(F.data.startswith("analyze_"))
async def cb_analyze_channel(callback: CallbackQuery):
    data = callback.data.replace("analyze_", "")
    user_id = get_user_id(callback.from_user.id)

    await callback.answer()

    if data == "all":
        await callback.message.edit_text("Анализирую все каналы...", parse_mode=None)
        try:
            raw_posts, analysis = agent.analyze_competitors(user_id)
            if raw_posts:
                await callback.message.answer(f"ПОСТЫ:\n\n{raw_posts[:3000]}", parse_mode=None)
                await send_post(callback.message, f"АНАЛИЗ:\n\n{analysis}")
            else:
                await send_post(callback.message, analysis)
        except Exception as e:
            await callback.message.answer(f"Ошибка: {e}", parse_mode=None)
        finally:
            agent.cleanup()
    else:
        await callback.message.edit_text(f"Анализирую {data}...", parse_mode=None)
        await _analyze_single(callback.message, user_id, data)


@dp.message(Command("ideas"))
async def cmd_ideas(message: Message):
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)

    await message.answer("Генерирую идеи...", parse_mode=None)
    result = agent.propose_ideas(user_id)
    await send_post(message, result)


@dp.message(Command("report"))
async def cmd_report(message: Message):
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)

    await message.answer("Готовлю отчёт...", parse_mode=None)
    result = agent.weekly_report(user_id)
    await send_post(message, result)


@dp.message(Command("news"))
async def cmd_news(message: Message):
    """Горячие новости из западных источников"""
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)

    await message.answer("Сканирую западные источники...", parse_mode=None)

    raw_news, ideas = agent.fetch_hot_news(user_id)

    if raw_news:
        # Показываем только идеи на русском, сырые новости скрываем
        await send_post(message, f"🔥 ГОРЯЧИЕ ТЕМЫ:\n\n{ideas}")
    else:
        await send_post(message, ideas)


@dp.message(Command("research"))
async def cmd_research(message: Message):
    """Пост с исследованием темы"""
    user_states[message.from_user.id] = {"state": "research_topic"}
    await message.answer("Что исследовать? Напиши тему, я найду актуальную инфу и напишу пост.", parse_mode=None)


@dp.message(Command("source"))
async def cmd_add_source(message: Message):
    """Добавить источник новостей"""
    user_states[message.from_user.id] = {"state": "add_source"}
    await message.answer(
        "Отправь ссылку на RSS или сайт.\n\n"
        "Примеры RSS для психологии:\n"
        "• https://psychcentral.com/feed\n"
        "• https://www.psychologytoday.com/intl/blog/feed\n\n"
        "Или любой другой RSS по твоей теме.",
        parse_mode=None
    )


@dp.message(Command("sources"))
async def cmd_list_sources(message: Message):
    """Список источников"""
    user_id = get_user_id(message.from_user.id)
    sources = agent.get_news_sources(user_id)

    buttons = []
    buttons.append([InlineKeyboardButton(text="➕ Добавить источник", callback_data="add_source")])

    if sources:
        text = "Твои источники новостей:\n\n"
        for s in sources:
            text += f"• {s['name']}\n"
        buttons.append([InlineKeyboardButton(text="🗑 Удалить все", callback_data="clear_sources")])
    else:
        text = "Нет источников.\n\nДобавь RSS-ленты по своей теме — я буду находить там идеи для постов."

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode=None)


@dp.message(Command("clear_sources"))
async def cmd_clear_sources(message: Message):
    """Удалить все источники"""
    user_id = get_user_id(message.from_user.id)
    db.execute("DELETE FROM memory_items WHERE user_id = ? AND content LIKE 'Источник:%'", (user_id,))
    await message.answer("Источники удалены", parse_mode=None)


@dp.message(Command("drafts"))
async def cmd_drafts(message: Message):
    """Список черновиков"""
    user_id = get_user_id(message.from_user.id)

    # Показываем и черновики, и отложенные
    drafts = db.fetch_all(
        """SELECT id, text, publish_at, status, created_at FROM drafts
           WHERE user_id = ? AND status IN ('draft', 'scheduled')
           ORDER BY
             CASE WHEN status = 'scheduled' THEN 0 ELSE 1 END,
             publish_at ASC,
             created_at DESC
           LIMIT 15""",
        (user_id,)
    )

    if not drafts:
        await message.answer("Нет черновиков", parse_mode=None)
        return

    buttons = []
    text = "📋 Черновики:\n\n"

    for i, (draft_id, draft_text, publish_at, status, created_at) in enumerate(drafts, 1):
        # Убираем HTML теги из preview
        import re
        clean_text = re.sub(r'<[^>]+>', '', draft_text)
        preview = clean_text[:40].replace('\n', ' ')

        if status == 'scheduled' and publish_at:
            # Форматируем время
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(publish_at)
                time_str = dt.strftime("%d.%m %H:%M")
                status_icon = f"⏰ {time_str}"
            except:
                status_icon = "⏰"
        else:
            status_icon = ""

        text += f"{i}. {preview}... {status_icon}\n\n"

        btn_text = f"⏰ {i}." if status == 'scheduled' else f"{i}."
        btn_preview = clean_text[:25]
        buttons.append([InlineKeyboardButton(
            text=f"{btn_text} {btn_preview}...",
            callback_data=f"viewdraft_{draft_id}"
        )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except:
        await message.answer(text, reply_markup=keyboard, parse_mode=None)


@dp.message(Command("calendar"))
async def cmd_calendar(message: Message):
    """Открыть календарь (Mini App)"""
    if not WEBAPP_URL:
        await message.answer(
            "Календарь пока не настроен.\n\n"
            "Для настройки укажите WEBAPP_URL в .env",
            parse_mode=None
        )
        return

    keyboard = calendar_keyboard()
    await message.answer(
        "📅 Планировщик постов\n\n"
        "Здесь можно:\n"
        "• Посмотреть расписание на неделю\n"
        "• Создать и запланировать посты\n"
        "• Перетащить посты на другую дату\n"
        "• Использовать AI для генерации",
        reply_markup=keyboard,
        parse_mode=None
    )


# ==================== ГОЛОСОВЫЕ ====================

@dp.message(F.voice)
async def handle_voice(message: Message):
    tg_id = message.from_user.id
    await message.answer("Распознаю голос...")

    try:
        text = await transcribe_voice(message.voice.file_id)
        await message.answer(f"Распознано: {text}", parse_mode=None)

        # Обрабатываем текст напрямую
        await process_text_input(message, text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", parse_mode=None)


# ==================== ПЕРЕСЛАННЫЕ СООБЩЕНИЯ ====================

@dp.message(F.forward_from_chat)
async def handle_forward(message: Message):
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)
    state = user_states.get(tg_id, {}).get("state")
    channel = message.forward_from_chat

    # Онбординг — подключение канала
    if state == "onboarding_channel":
        channel_name = f"@{channel.username}" if channel.username else channel.title
        agent.save_channel(user_id, str(channel.id), channel_name)

        await message.answer(f"Подключил {channel_name}. Анализирую...", parse_mode=None)

        # Автоанализ
        try:
            if channel.username:
                raw, analysis = agent.analyze_single_channel(user_id, f"@{channel.username}")
                if analysis and "Ошибка" not in analysis:
                    agent.save_style(user_id, f"Авто-анализ: {analysis[:500]}")
                    await send_post(message, f"Понял стиль:\n\n{analysis[:1000]}")
        except:
            pass

        # Спрашиваем про конкурентов
        user_states[tg_id] = {"state": "onboarding_competitors"}
        skip_btn = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить →", callback_data="skip_competitors")]
        ])
        await message.answer(
            "Какие каналы смотришь для идей? Напиши @username или пропусти.",
            reply_markup=skip_btn,
            parse_mode=None
        )
        return

    # Настройки — смена канала
    if state == "set_channel":
        channel_name = f"@{channel.username}" if channel.username else channel.title
        agent.save_channel(user_id, str(channel.id), channel_name)
        user_states.pop(tg_id, None)
        await message.answer(f"Канал подключен: {channel_name}", parse_mode=None, reply_markup=main_menu)
        return

    # Просто переслали — ничего не делаем
    if message.text:
        await process_text_input(message, message.text)


# ==================== ТЕКСТ ====================

async def process_text_input(message: Message, text: str):
    """Обработка текста (из сообщения или голоса)"""
    tg_id = message.from_user.id
    user_id = get_user_id(tg_id)
    state = user_states.get(tg_id, {}).get("state")

    # === ГЛАВНОЕ МЕНЮ ===
    if text == "🎤 Создать пост":
        await cmd_post(message)
        return
    elif text == "💡 Идеи на сегодня":
        await cmd_ideas(message)
        return
    elif text == "📋 Черновики":
        await cmd_drafts(message)
        return
    elif text == "⚙️":
        await message.answer("Настройки:", reply_markup=settings_menu, parse_mode=None)
        return

    # === МЕНЮ НАСТРОЕК ===
    elif text == "📺 Сменить канал":
        await cmd_channel(message)
        return
    elif text == "👥 Конкуренты":
        await cmd_list_competitors(message)
        return
    elif text == "📰 Источники":
        await cmd_list_sources(message)
        return
    elif text == "🎨 Стиль":
        await cmd_style(message)
        return
    elif text == "🔙 Назад":
        await show_main_screen(message)
        return

    # === LEGACY кнопки (для обратной совместимости) ===
    elif text == "📝 Создать пост":
        await cmd_post(message)
        return
    elif text == "🔥 Горячие темы":
        await cmd_news(message)
        return
    elif text == "💡 Идеи":
        await cmd_ideas(message)
        return
    elif text == "📊 Анализ":
        await cmd_analyze(message)
        return
    elif text == "⚙️ Настройки":
        await message.answer("Настройки:", reply_markup=settings_menu, parse_mode=None)
        return
    elif text == "🎨 Изменить стиль":
        await cmd_style(message)
        return
    elif text == "➕ Конкурент":
        await cmd_competitor(message)
        return
    elif text == "📰 Источник":
        await cmd_add_source(message)
        return
    elif text == "👥 Мои конкуренты":
        await cmd_list_competitors(message)
        return
    elif text == "🔍 Исследовать тему":
        await cmd_research(message)
        return
    elif text == "📋 Отчёт":
        await cmd_report(message)
        return

    # === ОНБОРДИНГ ===

    # Шаг 1: Подключение канала
    if state == "onboarding_channel":
        # Принимаем и @channel и просто channel
        clean_text = text.strip().lstrip("@")
        if clean_text and not " " in clean_text:
            channel_name = f"@{clean_text}"
            agent.save_channel(user_id, channel_name, channel_name)

            await message.answer(f"Подключил {channel_name}. Анализирую...", parse_mode=None)

            # Автоанализ канала
            try:
                raw, analysis = agent.analyze_single_channel(user_id, channel_name)
                if analysis and "Ошибка" not in analysis:
                    # Сохраняем стиль из анализа
                    agent.save_style(user_id, f"Авто-анализ: {analysis[:500]}")
                    await send_post(message, f"Понял стиль канала:\n\n{analysis[:1000]}")
            except Exception as e:
                await message.answer("Не смог прочитать канал, но это ок — буду учиться на твоих постах.", parse_mode=None)

            # Спрашиваем про конкурентов
            user_states[tg_id] = {"state": "onboarding_competitors"}

            skip_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить →", callback_data="skip_competitors")]
            ])
            await message.answer(
                "Какие каналы смотришь для вдохновения?\n\n"
                "Напиши @username или пропусти.",
                reply_markup=skip_btn,
                parse_mode=None
            )
            return
        else:
            await message.answer("Напиши @username канала или перешли пост из него", parse_mode=None)
            return

    # Шаг 2: Конкуренты (опционально)
    if state == "onboarding_competitors":
        # Принимаем и @channel и просто channel
        clean_text = text.strip().lstrip("@")
        if clean_text and not " " in clean_text:
            channel = f"@{clean_text}"
            agent.add_competitor(user_id, channel)

            skip_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Готово ✓", callback_data="skip_competitors")]
            ])
            await message.answer(
                f"Добавил {channel}. Ещё @username или нажми «Готово»",
                reply_markup=skip_btn,
                parse_mode=None
            )
            return
        else:
            await message.answer("Напиши @username канала", parse_mode=None)
            return

    # Шаг 3 (legacy): Стиль вручную
    if state == "onboarding_style":
        agent.save_style(user_id, text)
        user_states.pop(tg_id, None)
        await show_main_screen(message)
        return

    # Редактирование черновика
    if state == "editing_draft":
        draft_id = user_states[tg_id].get("draft_id")
        versions = user_states[tg_id].get("versions", [])

        row = db.fetch_one(
            "SELECT text FROM drafts WHERE id = ? AND user_id = ?",
            (draft_id, user_id)
        )

        if not row:
            await message.answer("Черновик не найден", parse_mode=None)
            user_states.pop(tg_id, None)
            return

        current_text = row[0]

        # Сохраняем текущую версию в историю
        if not versions:
            versions = [current_text]  # Оригинал
        if current_text != versions[-1]:
            versions.append(current_text)

        await message.answer("Редактирую...", parse_mode=None)

        # Используем LLM для редактирования с контекстом истории
        new_text = agent.edit_post_with_history(user_id, current_text, text, versions)

        # Сохраняем новую версию в историю
        versions.append(new_text)
        user_states[tg_id]["versions"] = versions

        # Сохраняем в БД
        db.execute(
            "UPDATE drafts SET text = ? WHERE id = ? AND user_id = ?",
            (new_text, draft_id, user_id)
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Ещё правка", callback_data=f"editdraft_{draft_id}"),
                InlineKeyboardButton(text="📝 Вручную", callback_data=f"manual_editdraft_{draft_id}")
            ],
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"viewdraft_{draft_id}")]
        ])

        await send_post(message, f"Готово:\n\n{new_text}", reply_markup=keyboard)
        return

    # Ручное редактирование черновика
    if state == "manual_editing_draft":
        draft_id = user_states[tg_id].get("draft_id")
        versions = user_states[tg_id].get("versions", [])
        original_text = versions[-1] if versions else ""

        edited_text = text.strip()
        # Если клиент прислал уже с форматированием — оставляем как есть
        if '<b>' in edited_text or '**' in edited_text:
            new_text = _markdown_to_html(edited_text)
        elif original_text:
            # LLM восстанавливает форматирование из оригинала
            new_text = _restore_formatting(original_text, edited_text)
        else:
            new_text = edited_text
        versions.append(new_text)

        # Сохраняем в БД
        db.execute(
            "UPDATE drafts SET text = ? WHERE id = ? AND user_id = ?",
            (new_text, draft_id, user_id)
        )

        user_states[tg_id] = {"state": "editing_draft", "draft_id": draft_id, "versions": versions}

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Ещё правка", callback_data=f"editdraft_{draft_id}"),
                InlineKeyboardButton(text="📝 Вручную", callback_data=f"manual_editdraft_{draft_id}")
            ],
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"viewdraft_{draft_id}")]
        ])

        await message.answer("✅ Текст обновлён", parse_mode=None)
        await send_post(message, new_text, reply_markup=keyboard)
        return

    # Ввод времени для отложенного поста
    if state == "set_publish_time":
        import re
        from datetime import datetime, timedelta

        draft_id = user_states[tg_id].get("draft_id")

        # Парсим время ЧЧ:ММ
        match = re.match(r'^(\d{1,2}):(\d{2})$', text.strip())
        if not match:
            await message.answer("Неверный формат. Напиши время как 14:30 или 09:00", parse_mode=None)
            return

        hour, minute = int(match.group(1)), int(match.group(2))

        if hour > 23 or minute > 59:
            await message.answer("Неверное время. Часы 0-23, минуты 0-59", parse_mode=None)
            return

        # Вычисляем дату публикации
        now = datetime.now()
        publish_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Если время уже прошло — завтра
        if publish_at <= now:
            publish_at += timedelta(days=1)

        # Сохраняем
        db.execute(
            "UPDATE drafts SET publish_at = ?, status = 'scheduled' WHERE id = ? AND user_id = ?",
            (publish_at.isoformat(), draft_id, user_id)
        )

        user_states.pop(tg_id, None)

        await message.answer(
            f"⏰ Запланировано на {publish_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            "Посмотреть: /drafts",
            reply_markup=main_menu,
            parse_mode=None
        )
        return

    # Канал через @username (из настроек)
    if state == "set_channel":
        if text.startswith("@"):
            agent.save_channel(user_id, text, text)
            user_states.pop(tg_id, None)
            await message.answer(
                f"Канал подключен: {text}",
                reply_markup=main_menu,
                parse_mode=None
            )
        else:
            await message.answer("Отправь @username или перешли сообщение из канала", parse_mode=None)
        return

    # Добавление конкурента
    if state == "add_competitor":
        channel = text if text.startswith("@") else f"@{text}"
        agent.add_competitor(user_id, channel)
        user_states.pop(tg_id, None)
        await message.answer(f"Конкурент добавлен: {channel}\n\nТеперь /analyze для анализа", parse_mode=None)
        return

    # Добавление источника новостей
    if state == "add_source":
        url = text.strip()
        if url.startswith("http"):
            # Пробуем получить название из URL
            name = url.split("/")[2].replace("www.", "")
            agent.add_news_source(user_id, url, name)
            user_states.pop(tg_id, None)
            await message.answer(
                f"Источник добавлен: {name}\n\n"
                "Теперь кнопка 🔥 Горячие темы будет читать и твои источники.",
                parse_mode=None
            )
        else:
            await message.answer("Отправь ссылку (начинается с http)", parse_mode=None)
        return

    # Исследование темы
    if state == "research_topic":
        await message.answer("Исследую тему и генерирую пост...", parse_mode=None)

        try:
            draft = agent.generate_post_with_research(user_id, text)
        except TaskLimitError:
            db.execute("UPDATE tasks SET status = 'cancelled' WHERE user_id = ? AND status IN ('queued', 'running', 'paused')", (user_id,))
            draft = agent.generate_post_with_research(user_id, text)

        pending_posts[tg_id] = draft
        user_states.pop(tg_id, None)

        await send_post(
            message,
            f"📰 Пост на основе исследования:\n\n{draft.text}",
            reply_markup=post_keyboard(draft.task_id)
        )
        return

    # Тема поста
    if state == "post_topic":
        await message.answer("Генерирую пост...")

        try:
            draft = agent.generate_post(user_id, text)
        except TaskLimitError:
            # Очищаем зависшие задачи
            db.execute("UPDATE tasks SET status = 'cancelled' WHERE user_id = ? AND status IN ('queued', 'running', 'paused')", (user_id,))
            draft = agent.generate_post(user_id, text)

        pending_posts[tg_id] = draft
        user_states.pop(tg_id, None)

        await send_post(message, draft.text, reply_markup=post_keyboard(draft.task_id))
        return

    # Тема поста в стиле конкретного канала
    if state == "post_topic_styled":
        target_channel = user_states[tg_id].get("target_channel", "")
        await message.answer(f"Генерирую пост в стиле {target_channel}...", parse_mode=None)

        # Добавляем канал в тему чтобы _extract_channel_from_topic нашёл его
        topic_with_channel = f"{text} в стиле {target_channel}"

        try:
            draft = agent.generate_post(user_id, topic_with_channel)
        except TaskLimitError:
            db.execute("UPDATE tasks SET status = 'cancelled' WHERE user_id = ? AND status IN ('queued', 'running', 'paused')", (user_id,))
            draft = agent.generate_post(user_id, topic_with_channel)

        pending_posts[tg_id] = draft
        user_states.pop(tg_id, None)

        await send_post(message, draft.text, reply_markup=post_keyboard(draft.task_id))
        return

    # Редактирование
    if state == "editing":
        old_draft = pending_posts.get(tg_id)
        if old_draft:
            # Получаем или создаём историю версий
            versions = user_states[tg_id].get("versions", [])
            if not versions:
                # Сохраняем оригинал как первую версию
                versions = [old_draft.text]

            # Архитектурно распознаём команды отката (без LLM)
            text_lower = text.lower()
            rollback_patterns = ['верни', 'откати', 'назад', 'предыдущ', 'оригинал', 'первый вариант', 'первоначальн']

            if any(p in text_lower for p in rollback_patterns):
                # Откат к предыдущей/первой версии
                if 'первый' in text_lower or 'оригинал' in text_lower or 'первоначальн' in text_lower:
                    # Первая версия (оригинал)
                    new_text = versions[0]
                    await message.answer("↩️ Возвращаю оригинал", parse_mode=None)
                elif len(versions) > 1:
                    # Предыдущая версия — убираем текущую из стека
                    versions.pop()
                    new_text = versions[-1]
                    await message.answer("↩️ Возвращаю предыдущую версию", parse_mode=None)
                else:
                    new_text = versions[0]
                    await message.answer("↩️ Это единственная версия", parse_mode=None)
            else:
                # Обычное редактирование
                await message.answer("Редактирую...", parse_mode=None)
                new_text = agent.edit_post(user_id, old_draft.text, text, topic=old_draft.topic)
                # Сохраняем новую версию в историю
                versions.append(new_text)

            old_draft.text = new_text
            pending_posts[tg_id] = old_draft
            # Сохраняем историю для следующих правок
            user_states[tg_id] = {"state": "editing", "versions": versions}

            await send_post(message, new_text, reply_markup=edit_keyboard(old_draft.task_id))
        return

    # Ручное редактирование — пользователь сам отправляет текст
    if state == "manual_editing":
        old_draft = pending_posts.get(tg_id)
        if old_draft:
            versions = user_states[tg_id].get("versions", [old_draft.text])
            original_text = versions[-1] if versions else old_draft.text

            edited_text = text.strip()
            # Если клиент прислал уже с форматированием — оставляем как есть
            if '<b>' in edited_text or '**' in edited_text:
                new_text = _markdown_to_html(edited_text)
            else:
                # LLM восстанавливает форматирование из оригинала
                new_text = _restore_formatting(original_text, edited_text)
            versions.append(new_text)

            old_draft.text = new_text
            pending_posts[tg_id] = old_draft
            user_states[tg_id] = {"state": "editing", "versions": versions}

            await message.answer("✅ Текст обновлён", parse_mode=None)
            await send_post(message, new_text, reply_markup=edit_keyboard(old_draft.task_id))
        return

    # Без состояния
    user_id = get_user_id(tg_id)

    # Если есть pending_post — это правка (без нажатия кнопки)
    old_draft = pending_posts.get(tg_id)
    if old_draft:
        await message.answer("Редактирую...", parse_mode=None)
        new_text = agent.edit_post(user_id, old_draft.text, text, topic=old_draft.topic)
        old_draft.text = new_text
        pending_posts[tg_id] = old_draft
        # Сохраняем версии
        versions = user_states.get(tg_id, {}).get("versions", [old_draft.text])
        versions.append(new_text)
        user_states[tg_id] = {"state": "editing", "versions": versions}
        await send_post(message, new_text, reply_markup=edit_keyboard(old_draft.task_id))
        return

    if agent.get_channel_id(user_id):
        # Канал подключен
        # Если это @username — спросим что делать (конкурент или тема)
        clean_text = text.strip().lstrip("@")
        if clean_text and " " not in clean_text and len(clean_text) >= 5 and text.startswith("@"):
            # Похоже на @username — добавим как конкурента
            channel = f"@{clean_text}"
            agent.add_competitor(user_id, channel)
            await message.answer(
                f"Добавил {channel} в конкуренты для анализа стиля.\n\n"
                "Напиши тему для поста или добавь ещё @канал.",
                reply_markup=main_menu,
                parse_mode=None
            )
            return

        # Генерим пост
        await message.answer("Генерирую пост...", parse_mode=None)

        try:
            draft = agent.generate_post(user_id, text)
        except TaskLimitError:
            db.execute("UPDATE tasks SET status = 'cancelled' WHERE user_id = ? AND status IN ('queued', 'running', 'paused')", (user_id,))
            draft = agent.generate_post(user_id, text)

        pending_posts[tg_id] = draft

        await send_post(message, draft.text, reply_markup=post_keyboard(draft.task_id))
    else:
        # Канал не подключен
        # Если это @username — сразу подключаем канал
        clean_text = text.strip().lstrip("@")
        if clean_text and " " not in clean_text and len(clean_text) >= 3:
            channel_name = f"@{clean_text}"
            agent.save_channel(user_id, channel_name, channel_name)
            await message.answer(f"Подключил {channel_name}. Анализирую...", parse_mode=None)

            # Автоанализ канала
            try:
                raw, analysis = agent.analyze_single_channel(user_id, channel_name)
                if analysis and "Ошибка" not in analysis:
                    agent.save_style(user_id, f"Авто-анализ: {analysis[:500]}")
                    await send_post(message, f"Понял стиль канала:\n\n{analysis[:1000]}")
            except Exception:
                await message.answer("Не смог прочитать канал, но это ок — буду учиться на твоих постах.", parse_mode=None)

            await message.answer("Готово! Теперь напиши тему для поста.", reply_markup=main_menu, parse_mode=None)
        else:
            # Просим подключить
            user_states[tg_id] = {"state": "onboarding_channel"}
            await message.answer(
                "Сначала подключи канал — напиши @username",
                parse_mode=None
            )


@dp.message(F.text)
async def handle_text(message: Message):
    """Обёртка для текстовых сообщений"""
    await process_text_input(message, message.text)


# ==================== КНОПКИ ====================

@dp.callback_query(F.data.startswith("pub_"))
async def cb_publish(callback: CallbackQuery):
    tg_id = callback.from_user.id
    user_id = get_user_id(tg_id)
    draft = pending_posts.get(tg_id)

    if not draft:
        await callback.answer("Пост не найден")
        return

    try:
        # Публикуем с HTML форматированием
        try:
            await bot.send_message(draft.channel_id, draft.text, parse_mode=ParseMode.HTML)
        except:
            await bot.send_message(draft.channel_id, draft.text)

        try:
            await callback.message.edit_text(f"{draft.text}\n\n✅ Опубликовано", parse_mode=ParseMode.HTML)
        except:
            await callback.message.edit_text(f"{draft.text}\n\n✅ Опубликовано", parse_mode=None)

        agent.approve_post(draft.task_id, user_id, draft.text)
        pending_posts.pop(tg_id, None)
        await callback.answer("Опубликовано!")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data == "skip_competitors")
async def cb_skip_competitors(callback: CallbackQuery):
    """Пропустить добавление конкурентов в онбординге"""
    tg_id = callback.from_user.id
    user_states.pop(tg_id, None)

    await callback.message.edit_text("Отлично! Всё готово.", parse_mode=None)
    await callback.message.answer(
        "Готов к работе. Скажи тему поста голосом или текстом.",
        reply_markup=main_menu,
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data == "regen")
async def cb_regenerate(callback: CallbackQuery):
    """Перегенерировать пост по той же теме"""
    tg_id = callback.from_user.id
    user_id = get_user_id(tg_id)

    # Берём тему из предыдущего поста
    old_draft = pending_posts.get(tg_id)
    if not old_draft or not old_draft.topic:
        user_states[tg_id] = {"state": "post_topic"}
        await callback.message.answer("О чём пост?")
        await callback.answer()
        return

    await callback.message.answer(f"Генерирую заново по теме: {old_draft.topic[:50]}...")
    await callback.answer()

    try:
        draft = agent.generate_post(user_id, old_draft.topic)
    except:
        db.execute("UPDATE tasks SET status = 'cancelled' WHERE user_id = ? AND status IN ('queued', 'running', 'paused')", (user_id,))
        draft = agent.generate_post(user_id, old_draft.topic)

    pending_posts[tg_id] = draft
    user_states.pop(tg_id, None)

    await send_post(callback.message, draft.text, reply_markup=post_keyboard(draft.task_id))


@dp.callback_query(F.data == "edit")
async def cb_edit(callback: CallbackQuery):
    # Отвечаем СРАЗУ чтобы не истёк timeout
    try:
        await callback.answer()
    except Exception:
        pass  # Игнорируем если query уже истёк

    tg_id = callback.from_user.id
    # Сохраняем текущие версии если есть
    old_versions = user_states.get(tg_id, {}).get("versions", [])
    old_draft = pending_posts.get(tg_id)

    # Если нет версий — инициализируем текущим текстом
    if not old_versions and old_draft:
        old_versions = [old_draft.text]

    user_states[tg_id] = {"state": "editing", "versions": old_versions}
    await callback.message.answer("Что изменить? Напиши или скажи голосом.\nНапример: сделай короче или добавь эмодзи", parse_mode=None)


@dp.callback_query(F.data == "manual_edit")
async def cb_manual_edit(callback: CallbackQuery):
    """Ручное редактирование — пользователь сам вводит текст"""
    # Отвечаем СРАЗУ чтобы не истёк timeout
    try:
        await callback.answer()
    except Exception:
        pass

    tg_id = callback.from_user.id
    old_draft = pending_posts.get(tg_id)

    if not old_draft:
        await callback.message.answer("Пост не найден")
        return

    # Сохраняем версии для возможности отката
    old_versions = user_states.get(tg_id, {}).get("versions", [])
    if not old_versions:
        old_versions = [old_draft.text]

    user_states[tg_id] = {"state": "manual_editing", "versions": old_versions}

    # Отправляем текст с форматированием
    await callback.message.answer(
        "📝 <b>Ручное редактирование</b>\n\n"
        "Скопируйте, отредактируйте и отправьте обратно.\n"
        "Форматирование сохранится автоматически.",
        parse_mode=ParseMode.HTML
    )
    # Отправляем текст С форматированием
    await send_post(callback.message, old_draft.text)


@dp.callback_query(F.data == "rollback")
async def cb_rollback(callback: CallbackQuery):
    """Откатить к предыдущей версии — архитектурно, без LLM"""
    tg_id = callback.from_user.id
    old_draft = pending_posts.get(tg_id)
    versions = user_states.get(tg_id, {}).get("versions", [])

    if not old_draft:
        await callback.answer("Пост не найден")
        return

    if len(versions) <= 1:
        await callback.answer("Это единственная версия", show_alert=True)
        return

    # Откатываем на предыдущую версию
    versions.pop()  # Убираем текущую
    new_text = versions[-1]  # Берём предыдущую

    old_draft.text = new_text
    pending_posts[tg_id] = old_draft
    user_states[tg_id] = {"state": "editing", "versions": versions}

    await send_post(callback.message, f"↩️ Откат:\n\n{new_text}", reply_markup=edit_keyboard(old_draft.task_id))
    await callback.answer("Откат выполнен")


@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery):
    tg_id = callback.from_user.id
    draft = pending_posts.pop(tg_id, None)
    user_states.pop(tg_id, None)

    if draft and draft.task_id:
        agent.reject_post(draft.task_id, get_user_id(tg_id), "отменено пользователем")

    await callback.message.edit_text("Отменено", parse_mode=None)
    await callback.answer()


# ==================== ЧЕРНОВИКИ ====================

@dp.callback_query(F.data.startswith("draft_"))
async def cb_save_draft(callback: CallbackQuery):
    """Сохранить в черновики"""
    tg_id = callback.from_user.id
    user_id = get_user_id(tg_id)
    draft = pending_posts.pop(tg_id, None)

    if not draft:
        await callback.answer("Пост не найден")
        return

    # Сохраняем в БД
    db.execute(
        """INSERT INTO drafts (user_id, text, topic, channel_id, status)
           VALUES (?, ?, ?, ?, 'draft')""",
        (user_id, draft.text, draft.topic, draft.channel_id)
    )

    try:
        await callback.message.edit_text(f"{draft.text}\n\n📋 <i>Сохранено в черновики</i>", parse_mode=ParseMode.HTML)
    except:
        await callback.message.edit_text(f"{draft.text}\n\n📋 Сохранено в черновики", parse_mode=None)
    await callback.answer("Сохранено! Открыть: /drafts")


@dp.callback_query(F.data.startswith("viewdraft_"))
async def cb_view_draft(callback: CallbackQuery):
    """Просмотр черновика"""
    draft_id = int(callback.data.replace("viewdraft_", ""))
    user_id = get_user_id(callback.from_user.id)

    row = db.fetch_one(
        "SELECT id, text, channel_id, publish_at, status FROM drafts WHERE id = ? AND user_id = ?",
        (draft_id, user_id)
    )

    if not row:
        await callback.answer("Черновик не найден")
        return

    draft_id, text, channel_id, publish_at, status = row

    # Разные кнопки для черновика и отложенного
    if status == 'scheduled' and publish_at:
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(publish_at)
            time_str = dt.strftime("%d.%m %H:%M")
        except:
            time_str = publish_at

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать сейчас", callback_data=f"pubdraft_{draft_id}")
            ],
            [
                InlineKeyboardButton(text=f"⏰ Изменить время ({time_str})", callback_data=f"schedule_{draft_id}")
            ],
            [
                InlineKeyboardButton(text="❌ Отменить расписание", callback_data=f"unschedule_{draft_id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"deldraft_{draft_id}")
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="back_drafts")]
        ])
        schedule_text = f"\n\n⏰ Выйдет: {time_str}"
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pubdraft_{draft_id}"),
                InlineKeyboardButton(text="⏰ Отложить", callback_data=f"schedule_{draft_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"editdraft_{draft_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"deldraft_{draft_id}")
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="back_drafts")]
        ])
        schedule_text = ""

    try:
        await callback.message.edit_text(f"{text}{schedule_text}", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except:
        await callback.message.edit_text(f"{text}{schedule_text}", reply_markup=keyboard, parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data.startswith("pubdraft_"))
async def cb_publish_draft(callback: CallbackQuery):
    """Опубликовать черновик"""
    draft_id = int(callback.data.replace("pubdraft_", ""))
    user_id = get_user_id(callback.from_user.id)

    row = db.fetch_one(
        "SELECT text, channel_id FROM drafts WHERE id = ? AND user_id = ?",
        (draft_id, user_id)
    )

    if not row:
        await callback.answer("Черновик не найден")
        return

    text, channel_id = row

    try:
        # Публикуем с HTML форматированием
        try:
            await bot.send_message(channel_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(channel_id, text)

        db.execute("UPDATE drafts SET status = 'published' WHERE id = ?", (draft_id,))

        # Сохраняем как успешный пост для обучения стилю
        agent.memory.store_decision(user_id, f"Опубликованный пост:\n{text}")

        try:
            await callback.message.edit_text(f"{text}\n\n✅ Опубликовано", parse_mode=ParseMode.HTML)
        except:
            await callback.message.edit_text(f"{text}\n\n✅ Опубликовано", parse_mode=None)
        await callback.answer("Опубликовано!")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("schedule_"))
async def cb_schedule_draft(callback: CallbackQuery):
    """Выбор времени публикации"""
    draft_id = callback.data.replace("schedule_", "")
    tg_id = callback.from_user.id

    # Сохраняем draft_id в состояние для ручного ввода
    user_states[tg_id] = {"state": "set_publish_time", "draft_id": int(draft_id)}

    await callback.message.edit_text(
        "Напиши время публикации в формате ЧЧ:ММ\n\n"
        "Например: 14:30 или 09:00\n\n"
        "Время по твоему часовому поясу.",
        parse_mode=None
    )
    await callback.answer()




@dp.callback_query(F.data.startswith("editdraft_"))
async def cb_edit_draft(callback: CallbackQuery):
    """Редактировать черновик"""
    draft_id = int(callback.data.replace("editdraft_", ""))
    tg_id = callback.from_user.id

    # Сохраняем версии если есть
    existing_versions = user_states.get(tg_id, {}).get("versions", [])
    user_states[tg_id] = {"state": "editing_draft", "draft_id": draft_id, "versions": existing_versions}

    await callback.message.answer(
        "Что изменить? Напиши или скажи голосом.\n"
        "Например: сделай короче, убери эмодзи, верни оригинал",
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("manual_editdraft_"))
async def cb_manual_edit_draft(callback: CallbackQuery):
    """Ручное редактирование черновика"""
    draft_id = int(callback.data.replace("manual_editdraft_", ""))
    tg_id = callback.from_user.id
    user_id = get_user_id(tg_id)

    # Получаем текст черновика
    draft_text = db.fetch_value(
        "SELECT text FROM drafts WHERE id = ? AND user_id = ?",
        (draft_id, user_id)
    )

    if not draft_text:
        await callback.answer("Черновик не найден")
        return

    # Сохраняем версии
    existing_versions = user_states.get(tg_id, {}).get("versions", [draft_text])
    user_states[tg_id] = {"state": "manual_editing_draft", "draft_id": draft_id, "versions": existing_versions}

    await callback.message.answer(
        "📝 <b>Ручное редактирование</b>\n\n"
        "Скопируйте, отредактируйте и отправьте обратно.\n"
        "Форматирование сохранится автоматически.",
        parse_mode=ParseMode.HTML
    )
    await send_post(callback.message, draft_text)
    await callback.answer()


@dp.callback_query(F.data.startswith("unschedule_"))
async def cb_unschedule_draft(callback: CallbackQuery):
    """Отменить расписание — вернуть в черновики"""
    draft_id = int(callback.data.replace("unschedule_", ""))
    user_id = get_user_id(callback.from_user.id)

    db.execute(
        "UPDATE drafts SET publish_at = NULL, status = 'draft' WHERE id = ? AND user_id = ?",
        (draft_id, user_id)
    )

    await callback.answer("Расписание отменено")
    # Показываем черновик заново
    await cb_view_draft(callback)


@dp.callback_query(F.data.startswith("deldraft_"))
async def cb_delete_draft(callback: CallbackQuery):
    """Удалить черновик"""
    draft_id = int(callback.data.replace("deldraft_", ""))
    user_id = get_user_id(callback.from_user.id)

    db.execute("DELETE FROM drafts WHERE id = ? AND user_id = ?", (draft_id, user_id))
    await callback.message.edit_text("🗑 Удалено", parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data == "back_drafts")
async def cb_back_drafts(callback: CallbackQuery):
    """Назад к списку черновиков"""
    # Имитируем вызов /drafts
    await cmd_drafts(callback.message)
    await callback.answer()


# ==================== ЗАПУСК ====================

async def main():
    print("=" * 40)
    print("SMM Agent запущен")
    print("Бот: @Yadro888_bot")
    print("Scheduler: включен (1 мин интервал)")
    print("=" * 40)

    # Инициализируем scheduler (60 сек для проверки отложенных постов)
    scheduler = SMMScheduler(db=db, llm=llm, bot=bot, check_interval=60)

    # Запускаем бота и scheduler параллельно
    await asyncio.gather(
        dp.start_polling(bot),
        scheduler.start()
    )


if __name__ == "__main__":
    asyncio.run(main())
