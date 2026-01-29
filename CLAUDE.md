# Yadro SMM

Telegram-бот + веб-приложение для SMM менеджеров с AI-генерацией постов (Claude Sonnet 4).

## Веб-сайт

**URL:** http://35.156.188.57

Веб-интерфейс для генерации постов и управления каналами. Использует то же AI-ядро что и Telegram бот.

### Функции
- **AI Агент** — генерация постов через Ядро SMM
- **Интеграции** — подключение Telegram/VK каналов
- **Календарь** — планирование публикаций
- **Аналитика** — статистика постов

### Технологии
- **Frontend:** Next.js 14, Tailwind CSS (Earth Core тема)
- **Backend:** FastAPI (общий с ботом)
- **Деплой:** PM2 + nginx

## Структура проекта

```
yadro-smm/
├── app/                    # Backend
│   ├── api/               # FastAPI REST API
│   ├── smm/               # Telegram бот + AI агент
│   ├── llm/               # Claude (Anthropic) + OpenAI
│   ├── executor/          # Plan → Steps архитектура
│   ├── kernel/            # TaskManager, очереди
│   ├── memory/            # Память пользователя (FTS5)
│   ├── storage/           # SQLite база данных
│   ├── tools/             # Инструменты (web_search, parse_channel)
│   └── scheduler/         # Планировщик публикаций
├── webapp/                 # Mini App (React + Vite)
├── tests/                  # Pytest тесты
├── data/                   # Базы данных, uploads
└── docs/                   # Документация
```

## Сервер

**AWS EC2 (Free Tier до декабря 2026):**
- **IP:** 35.156.188.57 (Elastic IP — постоянный)
- **User:** ubuntu
- **Бот:** /home/ubuntu/yadro-smm
- **Веб:** /home/ubuntu/yadro-post

### SSH подключение

```bash
ssh -i ~/Desktop/yadro-key.pem ubuntu@35.156.188.57
```

### Управление сервисами

```bash
# Telegram бот (systemd)
systemctl status yadro-bot
systemctl restart yadro-bot
journalctl -u yadro-bot -f

# Веб-сайт (PM2)
pm2 list                    # Статус всех процессов
pm2 logs yadro-web          # Логи фронтенда
pm2 restart yadro-web       # Рестарт фронтенда
```

### Деплой (из локальной папки)

```bash
./deploy.sh
```

Скрипт делает:
1. rsync файлов (исключая venv, __pycache__, *.db, .env)
2. systemctl restart yadro-bot

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `app/smm/bot.py` | Telegram бот (aiogram 3.x) |
| `app/smm/agent.py` | AI агент — генерация, редактирование |
| `app/executor/step_executor.py` | Выполнение шагов (LLM, tools) |
| `app/executor/plan_manager.py` | Создание планов (smm_generate, smm_analyze) |
| `app/tools/smm_tools.py` | Инструменты (web_search, parse_channel, compute_metrics) |
| `app/api/app.py` | FastAPI приложение |
| `run_all.py` | Запуск бота + API |
| `deploy.sh` | Деплой на сервер |

## Переменные окружения (на сервере в .env)

```env
TELEGRAM_BOT_TOKEN=8499179297:AAFDb...
ANTHROPIC_API_KEY=sk-ant-api03-uvD7C1m...
OPENAI_API_KEY=sk-proj-...
```

## Архитектура генерации постов

```
Тема → Plan (smm_generate) → Steps:
  1. memory_search — похожие посты из памяти
  2. web_search — актуальные данные из DuckDuckGo
  3. LLM_CALL (Claude) — генерация поста
  4. _apply_style_postprocess — жирный заголовок, эмодзи
```

## Автоподстройка температуры

При анализе канала (`/competitor @channel`) автоматически определяется:
- **content_type**: аналитический, новостной, лайфстайл, авторский, экспертный
- **recommended_temperature**: 0.3-0.7

Логика в `app/tools/smm_tools.py` → `compute_channel_metrics()`:
- Длинные + мало эмодзи → аналитический (0.3)
- Короткие + эмодзи → лайфстайл (0.7)
- Без эмодзи + без CTA → новостной (0.35)

## Инсайты от конкурентов

При генерации используются ИНСАЙТЫ (темы, идеи), но НЕ копируется стиль.
Метод `_extract_competitor_insights()` в `agent.py` фильтрует:
- ✅ Оставляет: темы, хуки, что работает
- ❌ Убирает: лицо повествования, структуру, эмодзи-паттерны

## Whitelist

```python
ALLOWED_USER_IDS = {140942228, 275622001, 727559198, 774618452}
```

## Последние изменения (2026-01-29)

### Веб-сайт (yadro-post)
- **Задеплоен веб-интерфейс** на http://35.156.188.57
- **Earth Core тема** — оранжево-красные цвета ядра Земли
- **Тёмная/светлая тема** — переключатель в сайдбаре
- **AI Агент** — генерация через Ядро SMM API
- **PM2 + nginx** — production деплой

### Бот (yadro-smm)
- **Claude Sonnet 4** как основная модель для генерации
- **Автоподстройка temperature** на основе метрик канала
- **Инсайты вместо копирования стиля** конкурентов
- **Дефолтное форматирование** — жирный заголовок + эмодзи по теме
- **Откат версий** — можно откатить несколько раз
- **Нормализация коротких команд** — "короче" → "сделай текст короче"

## Безопасность

### Rate Limiting (nginx)
- **AI генерация** (`/api/posts/generate`): 2 req/sec, burst 5
- **Остальные API**: 10 req/sec, burst 20
- При превышении — HTTP 429 Too Many Requests

### Security Headers
```
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
```

### Защита API ключей
- `.env` в `.gitignore` — ключи не попадают в git
- Ключи хранятся только на сервере

### Рекомендации
- Swagger UI (`/docs`) доступен — можно ограничить по IP
- Порты 8000, 8001, 3000 открыты внутри — nginx проксирует наружу

## Известные проблемы

- SSH может зависать если sshd перегружен (решение: reboot через AWS Console)
- Анализ канала 10-30 сек (LLM + парсинг)

## AWS Console

Если SSH не работает:
1. console.aws.amazon.com → EC2 → Instances
2. Найти инстанс yadro-smm (35.156.188.57)
3. Connect → EC2 Instance Connect

Детальная архитектура: `docs/ARCHITECTURE.md`
