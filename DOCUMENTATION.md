# Ядро SMM - Полная документация

## Обзор проекта

**Ядро SMM** — AI-агент ядро (мозг) для автоматизации задач. Универсальная система с многослойной архитектурой, поддержкой инструментов и Telegram интерфейсом.

Это **отдельный проект** от Ядро Post. Планировалось использовать как AI-ядро внутри Ядро Post.

---

## Архитектура (Слои)

```
yadro-smm/
├── app/
│   ├── config/        # Layer 0: Конфигурация
│   │   └── settings.py
│   │
│   ├── storage/       # Layer 1: Хранилище
│   │   ├── schema.py  # SQLite схема
│   │   └── files.py   # Файловое хранилище
│   │
│   ├── kernel/        # Layer 2: Task Kernel
│   │   ├── models.py      # Task, TaskStatus, TaskEvent
│   │   └── task_manager.py
│   │
│   ├── executor/      # Layer 3: Executor (Agent Loop)
│   │   ├── models.py      # Plan, Step, ExecutionContext
│   │   ├── executor.py    # Главный исполнитель
│   │   ├── plan_manager.py
│   │   └── step_executor.py
│   │
│   ├── tools/         # Layer 4: Инструменты
│   │   ├── models.py      # Tool, ToolParam
│   │   ├── registry.py    # ToolRegistry
│   │   ├── runtime.py     # ToolRuntime
│   │   ├── policy.py      # ToolPolicy (разрешения)
│   │   ├── browser.py     # Browser Tool
│   │   ├── web_search.py  # Web Search
│   │   ├── voice.py       # Voice (TTS/STT)
│   │   └── news_monitor.py
│   │
│   ├── llm/           # Layer 5: LLM Service
│   │   ├── models.py      # LLMRequest, LLMResponse
│   │   ├── prompts.py     # PromptBuilder
│   │   ├── router.py      # ModelRouter (выбор модели)
│   │   ├── cost_tracker.py # Отслеживание затрат
│   │   ├── service.py     # LLMService
│   │   └── openai_provider.py
│   │
│   ├── memory/        # Layer 6: Память
│   │   ├── models.py
│   │   └── service.py
│   │
│   ├── scheduler/     # Layer 7: Планировщик
│   │   ├── models.py
│   │   └── scheduler.py
│   │
│   ├── interfaces/    # Layer 8: Интерфейсы
│   │   └── telegram.py    # Telegram Bot Handler
│   │
│   └── smm/           # SMM специфика
│       └── scheduler_tasks.py
│
└── tests/             # Тесты
    ├── test_kernel.py
    ├── test_executor.py
    ├── test_tools.py
    └── ...
```

---

## Стек технологий

- **Python** 3.11+
- **SQLite** — локальная база данных (WAL mode)
- **OpenAI / Anthropic** — LLM провайдеры
- **python-telegram-bot** — Telegram интерфейс

---

## Компоненты

### 1. Task Kernel (Layer 2)

Управление задачами с state machine:

```python
class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TaskManager:
    def enqueue(user_id, input_text, task_type) -> Task
    def claim() -> Optional[Task]  # Берёт задачу в работу
    def succeed(task_id, result)
    def fail(task_id, error)
    def pause(task_id, reason)
    def resume(task_id)
    def cancel(task_id)
```

### 2. Executor (Layer 3)

Agent Loop — мозг системы:

```python
class Executor:
    # Worker Loop (фоновый поток)
    def start_worker(blocking=False)
    def stop_worker()

    # Agent Loop (выполнение задачи)
    def run_task(task: Task)

    # Лимиты безопасности
    max_steps = 20
    max_wall_time_seconds = 300
```

**Цикл выполнения:**
1. Claim task from Kernel
2. Build/Restore plan
3. Execute steps
4. Check limits
5. Handle approvals
6. Succeed/Fail/Pause

### 3. Tools (Layer 4)

Регистрация и выполнение инструментов:

```python
class Tool:
    name: str
    description: str
    parameters: List[ToolParam]
    requires_approval: bool
    execute: Callable

# Доступные инструменты:
- browser      # Браузер (Playwright)
- web_search   # Веб-поиск
- voice        # TTS/STT
- news_monitor # Мониторинг новостей
```

### 4. LLM Service (Layer 5)

```python
class LLMService:
    def complete_simple(prompt, user_id, task_id) -> str
    def complete(request: LLMRequest) -> LLMResponse

    # Rate Limiting
    max_requests_per_minute = 20
    max_cost_per_hour = 1.0  # $1/час
```

**Router** — выбор модели по задаче:
- `gpt-4o` — сложные задачи
- `gpt-4o-mini` — простые задачи
- `claude-sonnet` — альтернатива

**Cost Tracker** — отслеживание затрат на API

### 5. Telegram Interface (Layer 8)

```python
class TelegramBotHandler:
    # Команды
    /start  — приветствие
    /help   — справка
    /status — лимиты и статус
    /tasks  — список задач
    /cancel [id] — отмена задачи

    # Текстовые сообщения → создание задачи
```

**Rate Limiting:**
- 10 сообщений/минуту
- 60 сообщений/час
- Бан на 5 минут при превышении

**Whitelist** — управление доступом пользователей

---

## База данных (SQLite)

```sql
-- Пользователи
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    tg_id INTEGER UNIQUE,
    username TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT
);

-- Задачи
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    status TEXT,
    task_type TEXT,
    input_text TEXT,
    input_data TEXT,  -- JSON
    result TEXT,      -- JSON
    error TEXT,
    created_at TEXT,
    started_at TEXT,
    completed_at TEXT
);

-- Шаги выполнения
CREATE TABLE task_steps (
    id INTEGER PRIMARY KEY,
    task_id INTEGER,
    plan_id TEXT,
    step_id TEXT,
    step_index INTEGER,
    action TEXT,
    action_data TEXT,
    status TEXT,
    result TEXT,
    error TEXT
);

-- События аудита
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY,
    task_id INTEGER,
    event_type TEXT,
    event_data TEXT,
    created_at TEXT
);
```

---

## Конфигурация

### settings.py

```python
@dataclass
class DatabaseSettings:
    path: Path = Path("data/yadro.sqlite3")
    wal_mode: bool = True
    busy_timeout_ms: int = 5000

@dataclass
class StorageSettings:
    base_path: Path = Path("data")
    uploads_dir: str = "uploads"
    outputs_dir: str = "outputs"
    snapshots_dir: str = "snapshots"
```

### Переменные окружения

```bash
# LLM
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOW_ALL=false  # Разрешить всех или только whitelist
```

---

## Запуск

```bash
# 1. Создать venv
python -m venv venv
source venv/bin/activate

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Настроить .env
cp .env.example .env

# 4. Запустить
python -m app.main
```

---

## Тесты

```bash
# Все тесты
pytest

# Конкретный модуль
pytest tests/test_kernel.py
pytest tests/test_executor.py
pytest tests/test_telegram.py
```

---

## Интеграция с Ядро Post

Ядро SMM может использоваться как AI-ядро внутри Ядро Post:

1. **Импорт LLMService** для генерации контента
2. **TaskManager** для фоновых задач
3. **Executor** для сложных многошаговых операций

```python
# Пример интеграции
from yadro_smm.app.llm import LLMService
from yadro_smm.app.kernel import TaskManager

llm = LLMService()
response = llm.complete_simple(
    prompt="Напиши пост про криптовалюты",
    user_id=1,
    task_id=None
)
```

---

## Roadmap

- [ ] Интеграция Claude вместо OpenAI
- [ ] Больше инструментов (file_manager, code_executor)
- [ ] Web UI для мониторинга
- [ ] Мультитенантность
- [ ] Векторная память (embeddings)

---

## Контакты

Проект: **Ядро SMM**
Версия: 0.1.0 (alpha)
