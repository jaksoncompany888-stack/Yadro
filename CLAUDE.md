# Yadro SMM

Telegram-бот для SMM менеджеров с AI-генерацией постов.

## Структура проекта

```
yadro-smm/
├── app/                    # Backend
│   ├── api/               # FastAPI REST API
│   ├── smm/               # Telegram бот + AI агент
│   ├── llm/               # OpenAI интеграция
│   ├── memory/            # Память пользователя (FTS5)
│   ├── storage/           # SQLite база данных
│   ├── tools/             # Инструменты (поиск, парсинг)
│   ├── providers/         # Соцсети (Telegram, VK)
│   └── scheduler/         # Планировщик публикаций
├── webapp/                 # Mini App (React + Vite)
├── tests/                  # Pytest тесты
├── data/                   # Базы данных, uploads
└── docs/                   # Документация
```

## Деплой

**Текущая архитектура:**
- **AWS EC2** (3.121.215.231) — бот + API
- **Vercel** — webapp (HTTPS) + API proxy
- **Render** — отключен

### Команды сервера

```bash
# SSH подключение
ssh -i ~/Desktop/yadro-key.pem ubuntu@3.121.215.231

# Управление ботом
sudo systemctl status yadro-bot
sudo systemctl restart yadro-bot
sudo journalctl -u yadro-bot -f

# Обновление кода
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '*.db' \
  -e "ssh -i ~/Desktop/yadro-key.pem" . ubuntu@3.121.215.231:~/yadro-smm/
ssh -i ~/Desktop/yadro-key.pem ubuntu@3.121.215.231 "sudo systemctl restart yadro-bot"
```

## Локальная разработка

```bash
# Установка
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Запуск бота + API
python run_all.py

# Тесты
pytest tests/ -v
```

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `app/smm/bot.py` | Telegram бот (aiogram) |
| `app/smm/agent.py` | AI агент генерации постов |
| `app/api/app.py` | FastAPI приложение |
| `run_all.py` | Запуск бота + API вместе |

## Переменные окружения

```env
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
WEBAPP_URL=https://yadro-six.vercel.app
APP_ENV=development
```

## Правила разработки

- **Архитектура > Промпты** — логика в коде, LLM только исполняет
- **Инкрементальные изменения** — не ломать работающий код
- **Тесты обязательны** — 300+ тестов должны проходить

Детальная архитектура: `docs/ARCHITECTURE.md`
