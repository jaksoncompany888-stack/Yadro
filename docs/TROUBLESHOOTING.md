# Решение проблем Yadro SMM

## Сервер упал / не отвечает

### Симптомы
- SSH: "Connection refused" или "Connection timed out"
- Сайт не открывается

### Решение
1. Reboot через AWS Console:
   - console.aws.amazon.com → EC2 → Instances
   - Выбрать инстанс → Actions → Instance State → Reboot
2. Подождать 1-2 минуты
3. Попробовать SSH снова

## OOM Killer (Out of Memory)

### Симптомы
- `npm install` или `npm run build` убивает процесс
- В логах: "Killed" или "oom-killer"

### Проверка
```bash
dmesg | grep -i "out of memory"
journalctl -k | grep -i "oom"
free -h  # Должен быть swap
```

### Решение
Swap уже добавлен (1.8GB). Если пропал:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Диск заполнен (No space left on device)

### Проверка
```bash
df -h
```

### Очистка
```bash
# Журналы systemd (безопасно)
sudo journalctl --vacuum-time=3d

# node_modules (можно восстановить через npm install)
rm -rf /home/ubuntu/yadro-post/frontend/node_modules
rm -rf /home/ubuntu/yadro-post/frontend/.next

# Кэш npm
npm cache clean --force

# Старые логи
sudo rm -rf /var/log/*.gz
```

## Frontend не обновляется

### Симптомы
- Изменения в коде не видны на сайте

### Решение
```bash
cd /home/ubuntu/yadro-post/frontend
git pull
npm run build
pm2 restart yadro-post-frontend
```

## API возвращает 502 Bad Gateway

### Причина
Backend (FastAPI) не запущен

### Решение
```bash
sudo systemctl status yadro-smm
sudo systemctl restart yadro-smm
sudo journalctl -u yadro-smm -f  # Смотреть логи
```

## Telegram бот не отвечает

### Проверка
```bash
sudo systemctl status yadro-bot
sudo journalctl -u yadro-bot -f
```

### Решение
```bash
sudo systemctl restart yadro-bot
```

## Анализ канала зависает

### Причина
- Парсинг Telegram занимает 10-30 секунд
- LLM генерация медленная

### Решение
Это нормально. Подождать до 60 секунд.

## Permission denied при git pull

### Причина
Нет доступа к приватному репо

### Решение
На сервере должен быть настроен SSH ключ для GitHub:
```bash
cat ~/.ssh/id_rsa.pub  # Должен быть добавлен в GitHub
```

## Rate Limiting (429 Too Many Requests)

### Причина
Слишком много запросов к API

### Лимиты
- AI генерация: 2 req/sec
- Остальные API: 10 req/sec

### Решение
Подождать и повторить запрос через несколько секунд.
