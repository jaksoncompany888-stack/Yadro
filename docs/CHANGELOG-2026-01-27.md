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
- **IP:** 3.121.215.231
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
ssh -i ~/Desktop/yadro-key.pem ubuntu@3.121.215.231

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
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '*.db' --exclude 'node_modules' --exclude '.git' -e "ssh -i ~/Desktop/yadro-key.pem" . ubuntu@3.121.215.231:~/yadro-smm/

# Затем перезапустить бот
ssh -i ~/Desktop/yadro-key.pem ubuntu@3.121.215.231 "sudo systemctl restart yadro-bot"
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
│              3.121.215.231                   │
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

- `app/smm/bot.py` - онбординг, меню настроек
- `app/smm/agent.py` - паттерн замены эмодзи
- `run_all.py` - добавлен load_dotenv()
- `webapp/src/api/client.js` - API_BASE = '/api'
- `webapp/vercel.json` - rewrite на AWS API
