# Changelog 27 января 2026

## Исправления бота

### 1. Меню настроек
- Убраны пункты "Стиль" и "Источники" из меню настроек (временно скрыты)

### 2. Онбординг
- Исправлено: бот принимает username канала как с `@`, так и без (`testsmm8` и `@testsmm8`)
- Исправлено: полная инструкция показывается всем пользователям при /start
- Убрано сообщение "С возвращением" - теперь всегда показывается полная инструкция

### 3. Редактирование постов
- Добавлена поддержка паттерна "вместо X поставь Y" для замены эмодзи
- Пример: "вместо сердечка поставь огонек"
- Добавлен маппинг названий эмодзи на символы (сердечко, огонек, звездочка и др.)

## Деплой на AWS

### Создан EC2 инстанс
- **IP:** 35.156.188.57 (Elastic IP — постоянный)
- **Регион:** EU Frankfurt (eu-central-1)
- **Тип:** t2.micro (Free Tier)
- **ОС:** Ubuntu 24.04 LTS
- **SSH ключ:** ~/Desktop/yadro-key.pem

### Настроен systemd сервис
Бот автоматически запускается и перезапускается при падении.

**Файл сервиса:** `/etc/systemd/system/yadro-bot.service`

### Полезные команды

```bash
# Подключение к серверу
ssh -i ~/Desktop/yadro-key.pem ubuntu@35.156.188.57

# Статус бота
sudo systemctl status yadro-bot

# Перезапуск бота
sudo systemctl restart yadro-bot

# Логи в реальном времени
sudo journalctl -u yadro-bot -f

# Последние 50 строк логов
sudo journalctl -u yadro-bot --no-pager -n 50
```

### Обновление кода на сервере

```bash
# С локального компьютера (из папки yadro-smm)
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '*.db' --exclude 'node_modules' --exclude '.git' -e "ssh -i ~/Desktop/yadro-key.pem" . ubuntu@35.156.188.57:~/yadro-smm/

# Затем перезапустить бот
ssh -i ~/Desktop/yadro-key.pem ubuntu@35.156.188.57 "sudo systemctl restart yadro-bot"
```

## Архитектура

```
┌─────────────────┐     ┌──────────────────────┐
│  Telegram Bot   │     │       Vercel         │
│    (клиент)     │     │   (HTTPS webapp)     │
└────────┬────────┘     └──────────┬───────────┘
         │                         │
         │ WebApp URL              │ /api/* proxy
         ▼                         ▼
┌──────────────────────────────────────────────┐
│              AWS EC2 (t2.micro)              │
│              35.156.188.57                   │
│  ┌─────────────┐    ┌─────────────────────┐  │
│  │  Telegram   │    │    FastAPI (API)    │  │
│  │    Bot      │    │    порт 8000        │  │
│  └─────────────┘    └─────────────────────┘  │
│                                              │
│  nginx (порт 80) - статика + прокси API     │
└──────────────────────────────────────────────┘
```

## Сервисы

| Сервис | Роль | Статус |
|--------|------|--------|
| AWS EC2 | Бот + API | Активен |
| Vercel | Webapp (HTTPS) + API proxy | Активен |
| Render | - | Отключен |

## Стоимость AWS

- t2.micro входит в AWS Free Tier
- 750 часов/месяц бесплатно первый год
- После Free Tier: ~$8-10/месяц

## Security Group (порты)

- 22 (SSH)
- 80 (HTTP - nginx)
- 443 (HTTPS)
- 8000 (API - внутренний)

## Изменённые файлы

- `app/smm/bot.py` - онбординг, меню настроек, WhitelistMiddleware, callback fix
- `app/smm/agent.py` - паттерн замены эмодзи, метрики анализа каналов
- `app/tools/channel_parser.py` - парсинг реакций, подписчиков
- `app/tools/smm_tools.py` - метрики: reactions, forwards, engagement_rate
- `run_all.py` - load_dotenv(), graceful shutdown, signal handlers
- `webapp/src/api/client.js` - API_BASE = '/api'
- `webapp/vercel.json` - rewrite на AWS API
- `webapp/src/components/Calendar.jsx` - dark theme fix (bg-tg-bg)

## Вечерняя сессия (20:00-22:10)

### Whitelist и Rate Limits
- Добавлен `ALLOWED_USER_IDS` = {140942228, 275622001, 727559198, 774618452}
- Добавлен `DAILY_LIMIT` = 50 постов/день
- `WhitelistMiddleware` проверяет ВСЕ входящие сообщения и callback'и

### Метрики анализа каналов
- Подписчики (get_channel_info)
- Средние/макс просмотры
- Реакции (парсинг `.tgme_reaction`)
- Репосты/форварды
- Engagement rate = (reactions + forwards) / views * 100

### Fixes
- Парсинг подписчиков: убрали суффикс "SUBSCRIBERS" из числа
- Парсинг реакций: исправлено затирание переменной `text`
- Callback timeout: `callback.answer()` вызывается сразу
- Дублирование сообщений: `edit_text` вместо `send_post`

### Graceful Shutdown
- `TimeoutStopSec=10` в systemd
- `KillMode=mixed`
- Signal handler в `run_all.py`
