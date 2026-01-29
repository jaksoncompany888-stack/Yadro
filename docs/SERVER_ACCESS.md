# Доступ к серверу Yadro SMM

## AWS EC2

- **IP**: 35.156.188.57
- **Instance type**: t2.micro (1GB RAM)
- **OS**: Ubuntu 24.04
- **Region**: eu-central-1 (Frankfurt)

## SSH доступ

```bash
ssh -i /Users/mac/Desktop/yadro-key.pem ubuntu@35.156.188.57
```

**Важно**: пользователь `ubuntu`, НЕ `ec2-user`

## Ключ

- **Путь**: `/Users/mac/Desktop/yadro-key.pem`
- Права должны быть 400: `chmod 400 /Users/mac/Desktop/yadro-key.pem`

## Сервисы на сервере

| Сервис | Порт | Управление |
|--------|------|------------|
| Frontend (Next.js) | 3000 | PM2: `pm2 restart yadro-post-frontend` |
| Backend (FastAPI) | 8000 | systemd: `sudo systemctl restart yadro-smm` |
| Telegram Bot | 8001 | systemd: `sudo systemctl restart yadro-bot` |
| Nginx | 80 | `sudo systemctl restart nginx` |

## PM2 команды

```bash
pm2 list                    # Статус процессов
pm2 logs                    # Логи
pm2 restart all             # Перезапуск всех
pm2 restart yadro-post-frontend  # Перезапуск фронтенда
```

## Systemd команды

```bash
sudo systemctl status yadro-smm    # Статус бэкенда
sudo systemctl restart yadro-smm   # Перезапуск бэкенда
sudo journalctl -u yadro-smm -f    # Логи бэкенда в реальном времени
```

## Nginx

```bash
sudo nginx -t                      # Проверка конфига
sudo systemctl reload nginx        # Применить изменения
cat /etc/nginx/sites-available/yadro  # Посмотреть конфиг
```

## Swap (добавлен для стабильности)

```bash
free -h                            # Проверить swap
# Должно показывать ~1.8GB swap
```

## Пути на сервере

- Frontend: `/home/ubuntu/yadro-post/frontend`
- Backend: `/home/ubuntu/yadro-smm`
- Nginx config: `/etc/nginx/sites-available/yadro`
- Логи nginx: `/var/log/nginx/`

## Деплой фронтенда

```bash
cd /home/ubuntu/yadro-post/frontend
git pull
npm install
npm run build
pm2 restart yadro-post-frontend
```

## Деплой бэкенда

```bash
cd /home/ubuntu/yadro-smm
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart yadro-smm
```

## Если SSH не работает

1. Зайти в AWS Console: https://console.aws.amazon.com
2. EC2 → Instances → выбрать yadro-smm
3. Actions → Instance State → Reboot
4. Или использовать EC2 Instance Connect (кнопка Connect)

## GitHub репозитории

- **yadro-smm** (бэкенд + бот): `git@github.com:jaksoncompany888-stack/yadro-smm.git`
- **yadro-post** (фронтенд): `git@github.com:jaksoncompany888-stack/yadro-post.git`

## Локальные пути (Mac)

- yadro-smm: `/Users/mac/Desktop/yadro-smm`
- yadro-post: `/Users/mac/Desktop/yadro-post`
- SSH ключ: `/Users/mac/Desktop/yadro-key.pem`
