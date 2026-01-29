# AWS Server Access - Yadro SMM

## Сервер

- **Провайдер**: AWS (Amazon Web Services)
- **IP**: 35.156.188.57
- **Instance type**: t2.micro (1GB RAM)
- **OS**: Ubuntu 24.04
- **Region**: eu-central-1 (Frankfurt)

## SSH

```bash
ssh -i /Users/mac/Desktop/yadro-key.pem ubuntu@35.156.188.57
```

**Пользователь**: `ubuntu` (НЕ ec2-user!)

## Ключ

- **Путь**: `/Users/mac/Desktop/yadro-key.pem`
- **Права**: `chmod 400 /Users/mac/Desktop/yadro-key.pem`

## Бэкенд

Запускается через `run_all.py` (НЕ systemd):

```bash
# Перезапуск бэкенда
cd /home/ubuntu/yadro-smm
pkill -9 -f run_all.py
source venv/bin/activate
nohup python run_all.py > /tmp/yadro.log 2>&1 &

# Проверить статус
ps aux | grep run_all
curl localhost:8000/health

# Логи
tail -f /tmp/yadro.log
```

## Фронтенд (PM2)

```bash
pm2 list
pm2 restart yadro-post-frontend
pm2 logs yadro-post-frontend
```

## Деплой

### Бэкенд
```bash
cd /home/ubuntu/yadro-smm
git pull
pkill -9 -f run_all.py
source venv/bin/activate
nohup python run_all.py > /tmp/yadro.log 2>&1 &
```

### Фронтенд
```bash
cd /home/ubuntu/yadro-post/frontend
git pull
npm install
npm run build
pm2 restart yadro-post-frontend
```

## Память

Swap настроен (~1.8GB). Проверка:
```bash
free -h
```

## Если сервер не отвечает

1. AWS Console → EC2 → Instances
2. Выбрать инстанс
3. Actions → Instance State → Reboot

## Пути на сервере

- Backend: `/home/ubuntu/yadro-smm`
- Frontend: `/home/ubuntu/yadro-post/frontend`
- Nginx: `/etc/nginx/sites-available/yadro`
- Backend logs: `/tmp/yadro.log`

## Локальные пути (Mac)

- Backend: `/Users/mac/Desktop/yadro-smm`
- Frontend: `/Users/mac/Desktop/yadro-post`
- SSH ключ: `/Users/mac/Desktop/yadro-key.pem`
- Postiz (референс): `/Users/mac/Desktop/postiz-ref`

## ВАЖНО

- IP: **35.156.188.57** (AWS Frankfurt)
- Это НЕ Яндекс Облако!
- Пользователь: **ubuntu**
