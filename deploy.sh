#!/bin/bash
# Deploy script for yadro-smm
# ВАЖНО: НЕ трогает базу данных на сервере!

SERVER="ubuntu@35.156.188.57"
KEY="~/Desktop/yadro-key.pem"
REMOTE_PATH="/home/ubuntu/yadro-smm"

echo "🚀 Deploying yadro-smm..."

# Синхронизируем код, исключая:
# - venv (у сервера свой)
# - __pycache__ (кэш Python)
# - .git (не нужен на сервере)
# - data/*.db* (ВСЯ база данных - КРИТИЧЕСКИ ВАЖНО!)
# - .env (у сервера свои ключи)

rsync -avz \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'data/*.db' \
    --exclude 'data/*.db-shm' \
    --exclude 'data/*.db-wal' \
    --exclude 'data/*.db-journal' \
    --exclude '.env' \
    --exclude 'node_modules' \
    -e "ssh -i $KEY" \
    ./ $SERVER:$REMOTE_PATH/

echo "📦 Files synced"

# Перезапускаем сервис
ssh -i $KEY $SERVER "sudo systemctl restart yadro-bot"

echo "✅ Deploy complete!"

# Проверяем статус
ssh -i $KEY $SERVER "sudo systemctl status yadro-bot --no-pager | head -10"
