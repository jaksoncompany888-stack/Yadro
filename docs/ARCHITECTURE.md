# Yadro v0

AI-агент с браузерной автоматизацией, LLM интеграцией и Telegram ботом.

## ГЛАВНЫЙ ПРИНЦИП: Архитектура > Промпты

**Минимум промптов, максимум архитектуры.**

Это не prompt-based агент, а architecture-based:
- **Логика в коде** — Plan/Step/Tools определяют ЧТО делать
- **Промпты формальны** — LLM только исполняет шаги, не решает
- **Переиспользуемость** — архитектура работает с любыми промптами
- **Независимость** — смена LLM провайдера не требует переписывания

```
ПЛОХО (prompt-based):
  "Ты SMM-агент. Проанализируй канал, найди паттерны, сгенери пост..."
  → Вся логика в промпте, нет контроля

ХОРОШО (architecture-based):
  Plan: [parse_channel] → [extract_metrics] → [analyze_patterns] → [generate]
  → Каждый шаг контролируем, промпт минимален
```

**Когда добавляешь функционал:**
1. Сначала — какие Steps нужны?
2. Потом — какие Tools?
3. В конце — минимальный промпт для LLM шага

**ВАЖНО: Промпты НЕ запрещены!**
- Промпты — это fallback когда архитектура не справляется
- Сначала пробуй решить кодом (regex, парсинг, правила)
- Если код не покрывает случай → LLM с чётким промптом
- Промпт должен быть конкретным: что делать, чего НЕ делать
- НЕ бросай архитектуру и НЕ беги в промпты при первой сложности

**Паттерн: LLM понимает, код контролирует**

Когда нужно понять естественный язык (голос, свободный текст):
```
ПЛОХО:
  LLM получает текст + запрос → LLM возвращает изменённый текст
  → LLM добавляет отсебятину, меняет что не просили

ХОРОШО:
  LLM получает текст + запрос → LLM возвращает ЗАМЕНЫ [{old, new}, ...]
  → Код ПРОВЕРЯЕТ: old существует в тексте?
  → Код ПРИМЕНЯЕТ только валидные замены
  → Никакой отсебятины — LLM предлагает, код контролирует
```

Пример (редактирование поста — гибрид v7):
```python
# Запрос: "убери хэштеги и добавь хук"

# 1. Разбиваем на части
parts = _split_edit_request("убери хэштеги и добавь хук")
# → ["убери хэштеги", "добавь хук"]

# 2. Классифицируем каждую часть
_is_precise_edit("убери хэштеги")  # → True (regex)
_is_precise_edit("добавь хук")     # → False (нужен LLM)

# 3. Сначала ВСЕ precise операции (код, без LLM)
result = _precise_edit(text, "убери хэштеги")
# → хэштеги удалены

# 4. Потом creative (LLM возвращает готовый текст)
result = _creative_edit(result, "добавь хук")
# → хук добавлен в начало как отдельный абзац
```

## Правила для Claude

- **НЕ запускать бота** — пользователь запускает сам
- **НЕ делать pkill/kill** процессов бота без явной просьбы
- **НЕ ломать работающий код** — изменения инкрементально
- **Архитектура ядра** — Executor, Plan, Step, Tools, Memory
- **Промпты минимальны** — логика в Steps, не в тексте промпта

## Архитектура (8 слоёв)

```
Layer 0: Storage     (app/storage/)     - SQLite + файлы
Layer 1: Interfaces  (app/interfaces/)  - Telegram бот
Layer 2: Kernel      (app/kernel/)      - Task State Machine
Layer 2b: Scheduler  (app/scheduler/)   - Расписание задач
Layer 3: Executor    (app/executor/)    - Оркестратор выполнения
Layer 4: Tools       (app/tools/)       - Браузер, поиск, SMM tools
Layer 5: LLM         (app/llm/)         - OpenAI, роутинг, лимиты
Layer 6: Memory      (app/memory/)      - Память пользователя, FTS5
Layer 7: Providers   (app/providers/)   - Соцсети: Telegram, VK, Instagram
```

## Providers — Мультиплатформенный постинг

Архитектура провайдеров (вдохновлена Postiz, 26k+ звёзд):

```
app/providers/
├── __init__.py      # Экспорты
├── base.py          # SocialProvider (абстрактный класс)
├── telegram.py      # TelegramProvider (Bot API)
├── vk.py            # VKProvider (VK API + OAuth2)
├── manager.py       # ProviderManager (кросс-постинг)
└── instagram.py     # TODO
```

### SocialProvider (базовый класс)

```python
class SocialProvider(ABC):
    name: str = "base"
    max_text_length: int = 4096
    max_media_per_post: int = 10
    supports_media: bool = True
    supports_scheduling: bool = False

    @abstractmethod
    async def post(channel_id, text, media) -> PostResult

    @abstractmethod
    async def validate_channel(channel_id) -> bool

    async def delete_post(channel_id, post_id) -> bool
    async def edit_post(channel_id, post_id, new_text) -> PostResult
    async def schedule_post(channel_id, text, scheduled_time) -> PostResult
```

### TelegramProvider

```python
provider = TelegramProvider(bot_token="123:ABC")

# Постинг
result = await provider.post("@mychannel", "Hello!")
# → PostResult(success=True, post_id="123", url="https://t.me/mychannel/123")

# С медиа
media = [MediaItem(type=MediaType.IMAGE, url="https://...")]
result = await provider.post("@channel", "Caption", media=media)

# Проверка прав
valid = await provider.validate_channel("@mychannel")

# HTML форматирование
text = provider.format_text("**bold** and *italic*")
# → "<b>bold</b> and <i>italic</i>"
```

### VKProvider

```python
provider = VKProvider(app_id="123", app_secret="secret")

# 1. Получить URL для OAuth
auth_url, state = provider.get_auth_url("https://mysite.com/callback")
# → "https://oauth.vk.com/authorize?client_id=123&..."

# 2. Пользователь авторизуется, получаем code
token = await provider.exchange_code(code, redirect_uri)

# 3. Постинг в группу
provider.set_token(token)
result = await provider.post("-123456", "Привет ВК!")

# Получить управляемые группы
groups = await provider.get_managed_groups()
# → [VKGroup(id=123, name="Моя группа", can_post=True), ...]
```

### ProviderManager (кросс-постинг)

```python
manager = ProviderManager()

# Регистрируем провайдеры
manager.register_provider("telegram", TelegramProvider(bot_token="..."))
manager.register_provider("vk", VKProvider(app_id="...", app_secret="..."))

# Добавляем каналы пользователя
manager.add_channel(user_id=1, channel=UserChannel(
    platform=Platform.TELEGRAM,
    channel_id="@mychannel",
    channel_name="My Channel",
))
manager.add_channel(user_id=1, channel=UserChannel(
    platform=Platform.VK,
    channel_id="-123456",
    channel_name="Моя группа ВК",
))

# Кросс-постинг
result = await manager.cross_post(
    user_id=1,
    text="<b>Новый пост!</b>",  # HTML автоматически убирается для VK
    platforms=[Platform.TELEGRAM, Platform.VK],
)

# Результат
if result.all_success:
    print("Posted everywhere!")
elif result.partial_success:
    print(f"OK: {result.successful}, Failed: {result.failed}")
```

### Адаптация контента

Manager автоматически адаптирует контент под платформу:
- **Telegram:** HTML форматирование (`<b>`, `<i>`)
- **VK:** Убирает HTML (не поддерживается)
- **Длина:** Обрезает до лимита платформы

## API Layer — REST для Mini App и Web

```
app/api/
├── __init__.py     # Документация endpoints
├── app.py          # FastAPI приложение
├── models.py       # Pydantic модели
├── deps.py         # Dependencies (DB, auth, services)
├── posts.py        # CRUD постов + AI генерация
└── calendar.py     # Календарь
```

### Запуск API

```bash
# Development
python -m app.api.app
# или
uvicorn app.api.app:app --reload --port 8000
```

### Endpoints

```
GET  /health                    - Health check
GET  /api                       - API info

# Posts
POST   /api/posts               - Создать пост
GET    /api/posts               - Список постов (фильтры: status, platform, from_date, to_date)
GET    /api/posts/{id}          - Получить пост
PATCH  /api/posts/{id}          - Обновить пост
DELETE /api/posts/{id}          - Удалить пост
POST   /api/posts/{id}/publish  - Опубликовать сейчас

# AI
POST   /api/posts/generate      - Сгенерировать пост
POST   /api/posts/edit          - Отредактировать с AI

# Calendar
GET    /api/calendar            - Посты за период (?days=7 или ?start_date=&end_date=)
GET    /api/calendar/week       - Неделя (?offset=0)
GET    /api/calendar/month      - Месяц (?year=&month=)
GET    /api/calendar/today      - Сегодня
GET    /api/calendar/slots      - Слоты для публикации (?date=YYYY-MM-DD)
```

### Авторизация

Telegram Mini App передаёт `initData` в заголовке:
```
X-Telegram-Init-Data: query_id=...&user={"id":123}&auth_date=...&hash=...
```

Backend проверяет hash через HMAC с bot token.

## Mini App — React + Tailwind

```
webapp/
├── src/
│   ├── App.jsx              # Роутинг: calendar | editor | post
│   ├── components/
│   │   ├── Calendar.jsx     # Недельный календарь
│   │   └── PostEditor.jsx   # Редактор с AI
│   ├── hooks/
│   │   └── useTelegram.js   # Telegram WebApp SDK
│   └── api/
│       └── client.js        # API клиент
├── package.json
└── vite.config.js
```

### Запуск Mini App

```bash
cd webapp
npm install
npm run dev
# Откроется на http://localhost:5173
# API проксируется на http://localhost:8000
```

### Подключение к боту

```python
# В bot.py добавить кнопку
from aiogram.types import WebAppInfo, InlineKeyboardButton

keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(
        text="📅 Календарь",
        web_app=WebAppInfo(url="https://your-domain.com/")
    )]
])
```

### Функции Mini App

1. **Календарь** — неделя с постами, drag & drop (TODO)
2. **Редактор** — создание поста с AI
3. **AI кнопки:**
   - "✨ AI" — генерация по теме
   - "Применить" — редактирование через AI
4. **Платформы** — выбор TG/VK
5. **Расписание** — дата и время

## SMM Agent — ГОТОВ (архитектурная версия)

SMM Agent (`app/smm/`) теперь использует полную архитектуру ядра:

### Как работает generate_post()

```
Telegram Bot
    ↓
SMMAgent.generate_post(user_id, topic)
    ↓
TaskManager.enqueue() → Task #1 (status: queued)
    ↓
Executor.run_task(task)
    ↓
PlanManager.build_plan("smm_generate")
    ↓
Plan: [Step 1] → [Step 2] → [Step 3] → [Step 4]
    ↓
StepExecutor.execute(step) × N:
  1. TOOL_CALL memory_search    — поиск похожих постов в памяти
  2. TOOL_CALL web_search       — актуальная инфа (если нужно)
  3. LLM_CALL smm_generate_post — генерация текста
  4. APPROVAL                   — пауза для пользователя
    ↓
ApprovalRequired exception → Task.status = PAUSED
    ↓
return PostDraft(text=draft_text, task_id=task.id)
    ↓
Бот показывает пост пользователю
    ↓
Пользователь: ✅ Опубликовать
    ↓
agent.approve_post(task_id) → Task.status = SUCCEEDED
```

### Ключевые файлы SMM

| Файл | Строк | Назначение |
|------|-------|------------|
| `app/smm/agent.py` | ~850 | SMMAgent — использует Executor |
| `app/smm/bot.py` | ~1100 | Telegram бот (aiogram) |
| `app/smm/scheduler_tasks.py` | ~270 | Фоновые задачи |
| `app/executor/plan_manager.py` | ~290 | Шаблоны планов smm_generate, smm_analyze |
| `app/executor/step_executor.py` | ~490 | Handlers для LLM_CALL, TOOL_CALL, APPROVAL |
| `app/tools/smm_tools.py` | ~200 | SMM tools: parse_channel, web_search, memory_search |

### SMMAgent API

```python
agent = SMMAgent(db=db, llm=llm)

# Генерация (через Executor)
draft = agent.generate_post(user_id, topic, style=None) → PostDraft
draft = agent.generate_post_with_research(user_id, topic) → PostDraft

# Одобрение/отклонение
agent.approve_post(task_id, user_id, post_text)
agent.reject_post(task_id, user_id, reason)

# Редактирование (простой LLM — без архитектуры)
new_text = agent.edit_post(user_id, original, edit_request, topic="")
new_text = agent.edit_post_with_history(user_id, current, edit_request, versions)

# Память
agent.save_style(user_id, style)
agent.save_channel(user_id, channel_id, channel_name)
agent.add_competitor(user_id, channel, auto_analyze=True)
agent.save_feedback(user_id, feedback, post_text)
agent.build_smm_context(user_id, extra_style) → str

# Анализ
posts, analysis = agent.analyze_single_channel(user_id, channel)
posts, analysis = agent.analyze_competitors(user_id)
raw_news, ideas = agent.fetch_hot_news(user_id)
ideas = agent.propose_ideas(user_id)
report = agent.weekly_report(user_id)
```

### Plan Templates (plan_manager.py)

**smm_generate** — генерация поста:
```python
Steps:
1. TOOL_CALL memory_search   — поиск похожих в памяти
2. TOOL_CALL web_search      — актуальная инфа (опционально)
3. LLM_CALL smm_generate_post — генерация с контекстом
4. APPROVAL                   — ждём пользователя
```

**smm_edit** — НЕ ИСПОЛЬЗУЕТСЯ (упрощено в v6):
```
Сейчас редактирование — простой LLM вызов в agent.edit_post():
1. LLM читает текст + запрос
2. Возвращает операции [{action, text}]
3. КОД применяет
```

**smm_analyze** — анализ канала:
```python
Steps:
1. TOOL_CALL parse_channel   — парсинг постов
2. LLM_CALL smm_analyze_style — анализ стиля
3. TOOL_CALL memory_store    — сохранение результата
```

### SMM Tools (smm_tools.py)

```python
register_smm_tools(channel_parser, news_monitor, memory_service, llm_service)

# Зарегистрированные tools:
- parse_channel(channel, limit, top) → {posts: [...]}
- web_search(query, limit) → {results: [...]}
- fetch_news(limit_per_source) → {news: [...]}
- memory_search(user_id, query, limit) → {results: [...]}
- memory_store(user_id, content, memory_type, importance) → {success: bool}
- parse_edit_intent(edit_request, original_text) → {operations: [...]}
- apply_edit_operations(original_text, operations, generated) → {result: str}
```

### Бот: Telegram команды

```
/start      - Онбординг (канал → конкуренты → стиль)
/post       - Создать пост
/channel    - Сменить канал
/competitor - Добавить конкурента
/competitors - Список конкурентов
/analyze    - Анализ конкурентов
/news       - Горячие темы из западных источников
/ideas      - Идеи для постов
/research   - Пост с исследованием темы
/source     - Добавить источник новостей
/sources    - Список источников
/drafts     - Черновики
/report     - Недельный отчёт
/style      - Изменить стиль
```

### Бот: Меню

```
Главное меню:
├── 🎤 Создать пост
├── 💡 Идеи на сегодня
├── 📋 Черновики
└── ⚙️ Настройки
    ├── 📺 Сменить канал
    ├── 👥 Конкуренты
    ├── 📰 Источники
    ├── 🎨 Стиль
    └── 🔙 Назад
```

## Структура проекта

```
yadro-smm/
├── app/
│   ├── api/            # FastAPI REST API
│   ├── config/         # Settings, конфиги лимитов
│   ├── executor/       # Plan, Step, ExecutionContext, StepExecutor
│   ├── interfaces/     # TelegramBotHandler, rate limiting
│   ├── kernel/         # TaskManager, Task state machine
│   ├── llm/            # LLMService, ModelRouter, CostTracker
│   ├── memory/         # MemoryService, FTS5 search
│   ├── providers/      # Соцсети: Telegram, VK
│   ├── scheduler/      # Scheduler, cron
│   ├── smm/            # SMM Agent, Bot, Scheduler Tasks
│   ├── storage/        # Database (SQLite WAL), FileStorage
│   └── tools/          # BrowserTool, ToolRegistry, SMM tools
├── webapp/             # Telegram Mini App (React)
│   ├── src/
│   │   ├── components/ # Calendar, PostEditor
│   │   ├── hooks/      # useTelegram
│   │   └── api/        # API client
│   └── package.json
├── data/               # БД, uploads, outputs, snapshots
├── tests/              # Pytest тесты (301 тестов)
└── venv/
```

## Команды

```bash
# Активация
source venv/bin/activate

# Тесты
pytest tests/ -q --ignore=tests/test_browser.py
pytest tests/test_executor.py -v
pytest tests/test_kernel.py -v

# Запуск бота (делает пользователь)
python -m app.smm.bot

# Примеры
python examples/demo_full.py      # Демо всех слоёв
```

## Переменные окружения (.env)

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOT_USERNAME=@...
OPENAI_API_KEY=sk-...
APP_ENV=development
```

## Ключевые классы

### Storage (Layer 0)
```python
Database(path)              # SQLite, WAL mode, thread-safe
  .execute(sql, params)     # INSERT/UPDATE/DELETE
  .fetch_one/all/value()    # SELECT
  .transaction()            # Context manager

FileStorage(base_path)      # Файлы: uploads, outputs, snapshots
```

### Kernel (Layer 2) - Task State Machine
```python
TaskManager(db)
  .enqueue(user_id, task_type, input_text, input_data) → Task
  .claim(worker_id) → Task          # Атомарный захват из очереди
  .heartbeat(task_id, worker_id)    # Продление lease
  .pause(task_id, reason, data)     # APPROVAL | DEPENDENCY | RATE_LIMIT
  .resume(task_id)
  .succeed(task_id, result)
  .fail(task_id, error)             # С retry логикой
  .cancel(task_id)

# Состояния: created → queued → running → paused/succeeded/failed/cancelled
```

### Executor (Layer 3)
```python
Executor(db, task_manager, step_executor)
  .run_task(task)                   # Запуск через Plan/Step
  .handle_approval(task_id, approved, edited_content)

PlanManager()
  .build_plan(task_id, task_type, input_text, input_data) → Plan

StepExecutor(task_manager, llm_service)
  .execute(step, context) → result
  # Handlers: _handle_llm_call, _handle_tool_call, _handle_approval

Plan(task_id, steps)
  .get_next_step() → Step           # Учитывает depends_on
  .is_complete → bool

Step(action, action_data, depends_on)
  # Actions: LLM_CALL, TOOL_CALL, APPROVAL, CONDITION, AGGREGATE

ExecutionContext(task_id, user_id, plan)
  .step_results: Dict[str, Any]
  .add_step_result(step_id, result)
  .get_step_result(step_id) → Any
```

### LLM (Layer 5)
```python
LLMService(db, mock_mode, openai_api_key)
  .complete(messages, model, user_id, task_id) → LLMResponse
  .complete_simple(prompt, system_prompt) → str

Message.system(content)
Message.user(content)
Message.assistant(content)

LLMResponse:
  .content: str
  .model: str
  .total_tokens: int
  .cost_usd: float

# Модели: gpt-4o, gpt-4o-mini, claude-3-5-sonnet, mock
```

### Memory (Layer 6)
```python
MemoryService(db)
  .store(user_id, content, memory_type, importance) → MemoryItem
  .store_fact(user_id, content)
  .store_decision(user_id, content, source_task_id)
  .search(user_id, query, limit) → List[SearchResult]  # FTS5
  .build_context(user_id, query) → MemoryContext
  .get_facts(user_id) → List[MemoryItem]

# Типы: FACT, DECISION, CONTEXT, TASK, FEEDBACK
```

### Tools (Layer 4)
```python
ToolRegistry()
  .register(name, handler, impact, allowed_task_types, parameters)
  .get(name) → ToolSpec
  .list_for_task_type(task_type) → List[ToolSpec]

# SMM Tools (регистрируются при старте бота):
register_smm_tools(channel_parser, news_monitor, memory_service, llm_service)

ChannelParser()
  .get_top_posts(channel, limit) → List[ChannelPost]
  .get_recent_posts(channel, limit) → List[ChannelPost]

NewsMonitor()
  .fetch_all(limit_per_source) → List[NewsItem]
  .fetch_custom_rss(url, name, limit) → List[NewsItem]
  .search_duckduckgo(query, limit) → List[NewsItem]
```

## Схема БД

```sql
users (id, tg_id, username, settings JSON)
tasks (id, user_id, task_type, status, input_text, input_data, result, error, current_plan_id, current_step_id)
task_events (id, task_id, event_type, event_data JSON, step_id)
task_steps (id, task_id, plan_id, step_id, step_index, action, action_data, status, result, error)
schedules (id, user_id, task_spec JSON, cron, next_run_at)
memory_items (id, user_id, memory_type, content, importance, metadata)
memory_fts (content)  -- FTS5 virtual table
costs (id, user_id, task_id, model, input_tokens, output_tokens, cost_usd)
drafts (id, user_id, text, topic, channel_id, publish_at, status)
```

## Паттерны

- **State Machine** — Task: created→queued→running→{paused,succeeded,failed}
- **Distributed Locking** — lease_expires_at для предотвращения дубликатов
- **Dependency DAG** — Step.depends_on для порядка выполнения
- **Fallback Chain** — gpt-4o → gpt-4o-mini → mock
- **FTS5 Search** — Полнотекстовый поиск в памяти
- **Cost Tracking** — Все LLM вызовы логируются с ценой
- **ApprovalRequired** — Exception для паузы на approval

## Лимиты безопасности

```python
# TaskManager
max_queued_per_user: 10
max_active_per_user: 3
max_tasks_per_hour: 50

# LLMService
max_requests_per_minute: 10
max_requests_per_hour: 100
max_cost_per_request: $0.50
max_cost_per_hour: $5.00
max_cost_per_day: $20.00

# Executor
max_steps: 20
max_wall_time_seconds: 300

# ToolRuntime
timeout_seconds: 60
requires_approval: bool
```

## Зависимости

- `aiogram` — Telegram bot framework
- `openai` — OpenAI API
- `playwright` — Браузер
- `pydantic` — Валидация
- `pytest` — Тесты
- `requests`, `beautifulsoup4` — HTTP, парсинг
- Python 3.13+

## Память SMM Agent

Форматы хранения в memory_items:

```
Стиль: {описание стиля}
Канал: {name} (ID: {id})
Конкурент: {channel}
Источник: {name} | {url}
Удачный пост: {text} | Просмотры: {count}
Опубликованный пост: {text}
Стиль канала {name}: {analysis}
Анализ {channel}: {analysis}
Фидбек: {feedback} | Пост: {example}
Пример правки: '{request}' | Было: ... | Стало: ...
Тренд: {description}
```

## Стратегия продукта

### Позиционирование: Агент + Планировщик

**Анализ рынка показал:**
- 78% покупают SMM-инструменты ради расписания постов
- 23% ради AI-генерации
- Чистые AI-писатели (Jasper, Copy.ai) умирают — ChatGPT бесплатен
- Планировщики с AI (Buffer, Later, Postiz) — живут

**Почему Jasper умирает, а Buffer живёт:**
```
Jasper: "Напиши пост" → GPT → текст
        Конкурирует с бесплатным ChatGPT
        Нет привязки, легко уйти

Buffer: Расписание + аналитика + (AI как фича)
        ChatGPT не планирует посты
        Все посты в системе = switching cost
```

**Наше преимущество перед ChatGPT:**
- Помним стиль канала
- Анализируем конкурентов
- Учимся на фидбеке
- Контекст накапливается

### Два режима продукта

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   РЕЖИМ 1: АГЕНТ (быстро, на ходу)                              │
│   ────────────────────────────────                              │
│   Интерфейс: Telegram бот / голос                               │
│   Сценарий: "Напиши пост про новую акцию"                       │
│   Результат: Агент пишет в твоём стиле за 10 сек                │
│                                                                 │
│   Когда: В метро, между встречами, идея пришла                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   РЕЖИМ 2: ПЛАНИРОВЩИК (сесть и спланировать)                   │
│   ──────────────────────────────────────────                    │
│   Интерфейс: Mini App → потом веб                               │
│   Сценарий: Календарь, drag & drop, превью                      │
│   AI: Кнопка "Помоги написать" когда застрял                    │
│                                                                 │
│   Когда: Воскресенье вечером, планируешь неделю                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Целевая аудитория

**Сейчас (MVP):**
- Solo-предприниматели с Telegram-каналами
- Эксперты (коучи, консультанты)
- Небольшие бренды в России/СНГ

**Проблема которую решаем:**
> "Хочу вести канал, но нет времени писать каждый день.
> Хочу чтобы посты звучали как я, а не как ChatGPT."

### Техническая стратегия

**Почему Mini App первым:**
```
Mini App = обычный веб (React), открывается внутри Telegram
         ↓
Плюсы:
• Авторизация бесплатно (Telegram передаёт user_id)
• Не нужно отдельное приложение
• Пользователь уже в Telegram
         ↓
Переход в полноценный веб = добавить страницу логина
Остальной код тот же самый
```

**Что берём от Postiz:**
- ✅ UI/UX идеи (как выглядит календарь, превью)
- ✅ Архитектурные паттерны (providers, structured output)
- ❌ Код фронтенда (слишком связан с их бэкендом)
- ❌ LangGraph/Mastra (у нас свои Plan/Steps)

**Архитектура:**
```
Telegram Bot ◄──────────────────────► Mini App / Web
      │                                     │
      └──────────► Общий Backend ◄──────────┘
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    SMMAgent       Providers       Memory
    (генерация)    (TG, VK)       (стиль)
```

### Масштабирование ядра

**Принцип:** Ядро универсальное, SMM — первое применение.

```
Сейчас:                          Потом:
────────                         ──────
Memory      → стиль канала       → профиль клиента
Providers   → Telegram, VK       → email, SMS, CRM
Executor    → генерация постов   → любые задачи
Plan/Steps  → SMM workflow       → логистика, поддержка
```

**Не погрязнуть в SMM:**
- Держать слои разделёнными
- Бизнес-логика SMM только в `app/smm/`
- Ядро (`kernel`, `executor`, `memory`, `providers`) — переиспользуемое

---

## История изменений

### 2025-01-26 (v9): API Layer + Mini App

**Цель:** Добавить REST API и фронтенд для планировщика постов.

**Что добавлено:**

Backend (FastAPI):
```
app/api/
├── app.py          # FastAPI приложение
├── models.py       # Pydantic модели (PostCreate, CalendarResponse, etc.)
├── deps.py         # Telegram Mini App auth, dependencies
├── posts.py        # CRUD + publish + AI generate/edit
└── calendar.py     # Week/month/slots endpoints
```

Frontend (React + Tailwind):
```
webapp/
├── src/
│   ├── App.jsx              # Роутинг views
│   ├── components/
│   │   ├── Calendar.jsx     # Недельный календарь
│   │   └── PostEditor.jsx   # Редактор с AI
│   ├── hooks/useTelegram.js # WebApp SDK
│   └── api/client.js        # API клиент
└── package.json
```

**Ключевые фичи:**
- Telegram Mini App авторизация (HMAC валидация initData)
- CRUD постов с фильтрами
- Календарь (неделя/месяц/слоты)
- AI генерация и редактирование через API
- Мультиплатформенность (TG + VK selection)
- Tailwind с Telegram theme colors

**Тесты:** 14 новых API тестов (всего 301)

**Файлы:** `app/api/*`, `webapp/*`, `tests/test_api.py`

---

### 2025-01-26 (v8): Provider Architecture — мультиплатформенный постинг

**Цель:** Подготовка к кросс-постингу в VK и Instagram (архитектура от Postiz).

**Что добавлено:**

```
app/providers/
├── __init__.py      # Экспорты
├── base.py          # SocialProvider (абстрактный класс)
├── telegram.py      # TelegramProvider (Bot API)
├── vk.py            # VKProvider (VK API + OAuth2 + PKCE)
└── manager.py       # ProviderManager (кросс-постинг)
```

**Ключевые классы:**
- `SocialProvider` — абстрактный класс с методами `post()`, `validate_channel()`, `delete_post()`, `edit_post()`
- `TelegramProvider` — постинг через Bot API, HTML форматирование
- `VKProvider` — OAuth2 + PKCE, загрузка медиа на сервера VK, wall.post
- `ProviderManager` — координатор кросс-постинга, адаптация контента под платформу
- `PostResult`, `MediaItem`, `CrossPostResult` — data classes

**VK OAuth2 flow:**
```
1. get_auth_url() → OAuth URL с PKCE challenge
2. Пользователь авторизуется
3. exchange_code() → access_token
4. post() → wall.post с attachments
```

**Тесты:** 32 новых теста (всего 287)

**Файлы:** `app/providers/base.py`, `app/providers/telegram.py`, `app/providers/vk.py`, `app/providers/manager.py`, `tests/test_providers.py`

---

### 2025-01-26 (v7): Гибридное редактирование (precise + creative)

**Проблема v6:** LLM возвращал операции JSON, но не понимал контекстные запросы вроде "добавь абзац между 2 и 3" или "сделай переход плавнее".

**Решение: Два режима**

```
edit_post(original, request)
    │
    ├─► _split_edit_request() → ["убери абзац", "добавь хук"]
    │
    ├─► _is_precise_edit() → классификация каждой части
    │       - "убери абзац" → precise (regex)
    │       - "добавь хук" → creative (LLM)
    │
    ├─► Сначала ВСЕ precise операции (код, без LLM):
    │       _precise_edit() → удаляет абзац
    │
    └─► Потом creative (LLM возвращает готовый текст):
            _creative_edit() → добавляет хук
```

**Precise режим** (код контролирует):
- Убрать эмодзи (конкретный или все)
- Удалить абзац по номеру (первый, последний, N-ый)
- Выделить жирным конкретный текст
- Убрать хэштеги
- Замена текста на текст

**Creative режим** (LLM видит всё):
- Добавить абзац между X и Y
- Добавить хук/зацепку
- Сделать короче/длиннее
- Переписать в другом стиле
- Сделать переход плавнее

**Умная валидация длины в creative режиме:**
- "добавь/дополни/расширь" → разрешено до 5x оригинала
- "сократи/короче/убери/удали" → результат должен быть короче (max 1.2x)
- Остальное → до 3x оригинала

**Что изменено:**
- `agent.py`:
  - `edit_post()` — гибридный диспетчер
  - `_split_edit_request()` — разбивает запрос на части (защищает "N и M")
  - `_is_precise_edit()` — проверяет regex паттерны (+ нормализация голоса)
  - `_precise_edit()` — применяет точечные операции кодом
  - `_creative_edit()` — LLM видит нумерованные абзацы, умная валидация длины

**Нормализация голосового ввода:**
- "чёрным/чёрный" → "жирным" (голос путает)
- "смайлик/смайлики" → "эмодзи"
- "убери радугу" → распознаётся как эмодзи без слова "эмодзи"

**Примеры:**
```
"убери эмодзи 🌈" → precise
"убери последний абзац и добавь хук" → precise + creative
"добавь абзац про качество между 2 и 3" → creative (числа защищены)
"сделай переход плавнее" → creative
```

**Файлы:** `app/smm/agent.py`

---

### 2025-01-26 (v6): Упрощение редактирования + восстановление форматирования

**Проблема v5:** Гибридный подход (парсинг + LLM классификация + верификация) работал плохо — удалял лишние абзацы, не находил текст.

**Решение: Простой LLM-подход**

```
Запрос: "убери последний абзац и эмодзи 🔧"
    ↓
LLM читает текст + запрос
    ↓
Возвращает операции с ТОЧНЫМ текстом:
[
  {"action": "delete", "text": "🔧"},
  {"action": "delete", "text": "Весь текст последнего абзаца..."}
]
    ↓
КОД применяет (с fuzzy whitespace matching)
```

**Ручное редактирование — восстановление форматирования:**
```
Клиент видит красивый текст с жирным
    ↓
Копирует, редактирует, отправляет (форматирование теряется)
    ↓
LLM восстанавливает <b> из оригинала
    ↓
Если клиент прислал с <b> или ** — сохраняем как есть
```

**Что изменено:**
- `agent.py`:
  - `edit_post()` — упрощён, без парсинга и верификации
  - `_analyze_edit_request()` — LLM возвращает операции с точным текстом
  - `_apply_operation()` — применяет delete/replace/insert
  - Убраны: `_parse_edit_intent`, `_classify_intent_with_llm`, `_verify_edit`
- `bot.py`:
  - `_restore_formatting()` — LLM восстанавливает жирный из оригинала
  - `_markdown_to_html()` — конвертация `**` → `<b>`
  - Ручное редактирование показывает форматированный текст
- `step_executor.py`:
  - `_markdown_to_html()` — очистка дублей `<b><b>` → `<b>`

**Текущая архитектура:**
| Функция | Подход | Memory | Web | Plan/Steps |
|---------|--------|--------|-----|------------|
| Генерация | Архитектура | ✅ | ✅ | ✅ |
| Анализ канала | Архитектура | ✅ | ❌ | ✅ |
| Редактирование | Простой LLM | ❌ | ❌ | ❌ |
| Ручное редактир. | LLM формат | ❌ | ❌ | ❌ |

**Файлы:** `app/smm/agent.py`, `app/smm/bot.py`, `app/executor/step_executor.py`

---

### 2025-01-26 (v5): Архитектура редактирования + гибридный подход (устарело)

**Проблема:** При редактировании LLM "тупел" — не понимал команды, добавлял хук в конец вместо начала, не выполнял все просьбы.

**Решение: Гибридная архитектура редактирования**

```
Запрос клиента: "добавь хук и убери хэштеги"
    ↓
1. CODE парсит известные паттерны (regex)
   → найдено: delete_hashtags
   → не найдено: "добавь хук"
    ↓
2. LLM классифицирует нераспознанное
   → "добавь хук" → add_hook
    ↓
3. LLM генерирует ТОЛЬКО новый контент
   → видит весь пост для контекста
   → генерит только хук, не весь текст
    ↓
4. CODE применяет изменения точечно
   → хук вставляется в начало
   → хэштеги удаляются
```

**Принцип:** LLM видит всё, но меняет только то, что просят.

**Что изменено:**
- `agent.py`:
  - `edit_post()` — полная переработка с архитектурным подходом
  - `_parse_edit_intent()` — CODE парсит известные паттерны
  - `_classify_intent_with_llm()` — LLM классифицирует нераспознанное
  - `_generate_edit_content()` — LLM генерит только нужный контент
  - `_apply_edit_operations()` — CODE применяет изменения
  - `_find_with_fuzzy_whitespace()` — нечёткое сравнение whitespace (`\n` vs `\n\n`)
  - `fetch_hot_news()` — теперь берёт посты только из источников пользователя
  - `propose_ideas()` — теперь парсит свежие посты конкурентов
- `bot.py`:
  - Добавлена кнопка "📝 Вручную" для быстрого редактирования без LLM
  - Исправлен маппинг "💡 Идеи на сегодня" → `cmd_ideas` (было `cmd_news`)
  - Добавлены состояния `manual_editing`, `manual_editing_draft`
- `scheduler_tasks.py`:
  - Исправлен синтаксис — `while self._running:` перенесён в `_run_loop()`
  - Отключены автоматические дайджесты (слишком навязчиво)
- `plan_manager.py`:
  - Добавлен шаблон `smm_edit` с 5 шагами

**Операции редактирования:**
```python
OPERATIONS = [
    "add_hook",           # хук/цепляющее начало
    "delete_paragraph",   # удалить абзац
    "rewrite_paragraph",  # переписать абзац
    "delete_hashtags",    # убрать хэштеги
    "add_hashtags",       # добавить хэштеги
    "add_paragraph",      # добавить абзац (after/before/position)
    "bold_text",          # выделить жирным
    "unbold_text",        # убрать жирный
    "shorten",            # сократить
    "expand",             # расширить
    "replace_text",       # заменить текст
]
```

**Файлы:** `app/smm/agent.py`, `app/smm/bot.py`, `app/smm/scheduler_tasks.py`, `app/executor/plan_manager.py`

---

### 2025-01-25 (v4): Поиск канала по словам + алиасы
**Что изменено:**
- `agent.py` — добавлены методы:
  - `_generate_channel_aliases()` — генерация алиасов при добавлении канала
  - `_translit_to_russian()` — латиница → кириллица
  - `_find_channel_by_keyword()` — поиск по алиасам в metadata
  - `_fuzzy_match()` — нечёткое сравнение (60% общих символов)
  - `_get_translit_variants()` — варианты транслитерации (ou↔ow, k↔c)
- `add_competitor()` — теперь сохраняет алиасы в metadata

**Как работает:**
```
Голос: "в стиле москоутиндер"
  → regex извлекает "москоутиндер"
  → ищем в metadata.aliases каждого канала
  → находим "@moscowtinder" (алиас "москоутиндер")
```

**Файлы:** `app/smm/agent.py`

---

### 2025-01-25 (v3): Жирный текст + релевантный стиль по теме
**Что изменено:**
- `step_executor.py`:
  - `_markdown_to_html()` — конвертация `**text**` → `<b>text</b>`
  - Применяется после LLM в `_handle_llm_call()` для smm_generate
  - Убраны инструкции про HTML из промптов
- `agent.py`:
  - `_markdown_to_html()` применяется в `edit_post()` и `edit_post_with_history()`
  - `_find_relevant_channel_styles()` — FTS5 поиск топ-3 релевантных каналов по теме
  - `build_smm_context(topic=...)` — ищет релевантные стили

**Как работает:**
```
Тема: "ретроградный меркурий"
  → FTS5 MATCH "ретроградный OR меркурий"
  → находит каналы про астрологию
  → контекст содержит только их стили
```

**Файлы:** `app/executor/step_executor.py`, `app/smm/agent.py`

---

### 2025-01-25 (v2): Автоматическая переаналитика + версионирование
**Что изменено:**
- `scheduler_tasks.py`:
  - `_reanalyze_outdated_channels()` — при старте проверяет все каналы
  - Ночной скан учитывает `analysis_version` в metadata
- `smm_tools.py`:
  - `memory_store()` — удаляет старый анализ перед сохранением нового
  - Добавлен параметр `metadata` с `analysis_version`
- `step_executor.py`:
  - Добавляет `analysis_version: "v2"` в metadata при сохранении

**Файлы:** `app/smm/scheduler_tasks.py`, `app/tools/smm_tools.py`, `app/executor/step_executor.py`

---

### 2025-01-25 (v1): Глубокий анализ каналов + UI конкурентов
**Что изменено:**
- `smm_tools.py` — добавлен `compute_channel_metrics()` (анализ БЕЗ LLM)
- `plan_manager.py` — smm_analyze: 4 шага (parse → metrics → deep_analyze → store)
- `step_executor.py` — handler `smm_deep_analyze` с готовыми метриками
- `bot.py`:
  - UI конкурентов: нажатие → меню (Анализировать/Удалить)
  - Кнопка "✍️ Написать пост в этом стиле" после анализа
  - Состояние `post_topic_styled`
- `plan_manager.py` — 50 постов вместо 15 для анализа

**Файлы:** `app/tools/smm_tools.py`, `app/executor/plan_manager.py`, `app/executor/step_executor.py`, `app/smm/bot.py`

---

### 2025-01-24: SMM Agent переведён на архитектуру
**Что изменено:**
- `agent.py` — переписан, использует Executor → Plan → Steps
- `plan_manager.py` — добавлены шаблоны smm_generate, smm_analyze
- `step_executor.py` — добавлены SMM handlers (_build_smm_prompt)
- `schema.py` — добавлена таблица drafts
- Все 255 тестов проходят

**Файлы:** `app/smm/agent.py`, `app/executor/plan_manager.py`, `app/executor/step_executor.py`, `app/storage/schema.py`

---

## TODO: Идеи по улучшениям

### Архитектурные (приоритет):

1. **A/B тестирование постов** — генерить 2-3 варианта, трекать какой зашёл лучше
   - Tool: `generate_variants` → 3 версии
   - Memory: хранить результаты по вариантам
   - Обучение: со временем понимать что работает

2. **Автоматический сбор метрик** — Scheduler задача
   - Раз в день парсить свой канал
   - Сохранять просмотры/реакции
   - Строить тренды

3. **Контент-календарь** — планирование на неделю
   - Tool: `plan_content_week` — анализ трендов + предложение тем
   - UI: показ плана, редактирование
   - Scheduler: напоминания

4. **Цепочки постов** — серии связанных постов
   - Plan template: `smm_series` — 3-5 постов на тему
   - Memory: связь между постами серии

### UI/UX:

5. **Быстрые шаблоны** — кнопки для частых типов постов
   - "Новость", "Лайфхак", "Опрос", "Мнение"
   - Каждый — свой Plan template

6. **Предпросмотр в канале** — перед публикацией
   - Показать как будет выглядеть
   - С форматированием

7. **Статистика в боте** — /stats
   - Сколько постов за неделю
   - Средние просмотры
   - Лучший пост

### Технические:

8. **Кэширование анализа** — не парсить канал каждый раз
   - TTL 24 часа для конкурентов
   - Инвалидация при явном /analyze

9. **Retry логика для парсера** — Telegram иногда блокирует
   - Exponential backoff
   - Прокси поддержка

10. **Миграция на async** — agent.py
    - Сейчас синхронный
    - aiogram async, но agent блокирует

---

## Бизнес-модель и тарифы

### Себестоимость (OpenAI GPT-4o)

| Операция | Стоимость |
|----------|-----------|
| Генерация поста | $0.01-0.02 |
| Анализ канала | $0.02-0.03 |
| Редактирование | $0.005-0.01 |

### Тарифная сетка

| | **Free** | **Pro** | **Business** | **Agency** |
|---|---|---|---|---|
| **Цена** | 0₽ | 1 990₽/мес | 4 990₽/мес | 14 990₽/мес |
| **Постов/мес** | 10 | 60 | 200 | ∞ |
| **Конкуренты** | 1 | 5 | 15 | ∞ |
| **Сайты/RSS** | 0 | 5 | 15 | ∞ |
| **Каналов** | 1 | 3 | 10 | ∞ |
| **Черновики** | 24ч | ∞ | ∞ | ∞ |
| **Отложенные** | ❌ | ✅ | ✅ | ✅ |
| **Анализ** | Ручной | 1р/день | Real-time | Real-time |
| **Twitter мониторинг** | ❌ | ❌ | ❌ | ✅ |
| **Маржа** | — | ~95% | ~93% | ~93% |

### Докупы (только Free)

| Докуп | Цена |
|-------|------|
| +5 постов | 149₽ |
| +1 конкурент навсегда | 299₽ |
| Глубокий анализ | 99₽ |

### Психология Free → Paid

**Принцип:** Дать попробовать ЛУЧШЕЕ качество, создать привычку, потом — лимит.

- 10 постов = ~2 недели активного теста
- Качество НЕ урезаем (чтобы впечатлился)
- Черновики 24ч (мотивация не терять работу)
- Лимит бьёт когда уже привык

**Триггеры конверсии:**
- Счётчик: "Осталось 2 поста из 10"
- Потеря: "Черновик удалится через 4 часа"
- Момент лимита: "Купить +5 постов за 149₽ или ждать 25 дней"

### Киллер-фича Agency: "Первый в Telegram"

```
Twitter/X, Reddit, западные СМИ → мониторинг каждые 5 мин
    ↓
Новый тренд появился
    ↓
Мгновенный алерт + готовый драфт
    ↓
Клиент публикует первым в РУ-сегменте
```

---

## Технические ограничения

### Парсинг каналов

- Через `t.me/s/channel` (веб-версия)
- **Лимит: ~20 последних постов**
- Для больше постов нужен Telegram API

### Защита от блокировок

```
Клиент добавил конкурента
    ↓
Уже в кэше? (< 24ч) → отдаём кэш
    ↓
Нет → очередь на ночной парсинг
    ↓
Ночью: 1 канал / 30 сек
    ↓
Утром: свежий анализ у всех
```

### TODO: Telegram API

- Библиотека: telethon или pyrogram
- Нужна авторизация через номер
- Даст: 100+ постов, приватные каналы

---

## Редактирование: Гибридный подход (TODO)

### Проблема текущего подхода
- LLM возвращает операции `[{action: "delete", text: "..."}]`
- Код применяет
- НО: LLM не понимает "между 2 и 3 абзацем" — не видит контекст

### Решение: Два режима

**1. Точечные операции** (код контролирует):
- Убрать эмодзи
- Удалить конкретный абзац
- Выделить жирным
- Убрать хэштеги

**2. Творческие операции** (LLM видит всё):
- Добавить абзац между X и Y
- Сделать короче/длиннее
- Переписать в другом стиле
- Добавить что-то в тему

### Промпт для творческого режима

```python
def edit_post(original, request):
    prompt = f"""
    ТЕКСТ: {original}
    
    ЗАПРОС: {request}
    
    ПРАВИЛА:
    - Сохрани ключевые факты (даты, цифры, условия)
    - Можешь подправить соседние предложения для связности
    - НЕ добавляй новых фактов от себя
    - НЕ удаляй то, что не просили
    - Стиль и тон — как в оригинале
    
    Верни готовый текст.
    """
    return llm.complete(prompt)
```

### Принцип
- Код минимальный — только отправляет промпт
- LLM умный — пусть сам разбирается
- Никаких операций, парсинга JSON, сложной логики
