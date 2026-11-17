#!/usr/bin/env bash
# Deploy to the VPS: ./deploy.sh   (override with DEPLOY_HOST=user@host)
set -euo pipefail
HOST="${DEPLOY_HOST:-root@187.124.208.220}"
APP=/var/www/directquotes
cd "$(dirname "$0")"
ssh "$HOST" "mkdir -p $APP"
rsync -az --delete public/ "$HOST:$APP/public/"
rsync -az --delete server/ "$HOST:$APP/server/"
rsync -az --delete deploy/ "$HOST:$APP/deploy/"
rsync -az --ignore-existing .env.example "$HOST:$APP/.env.example"
ssh "$HOST" "bash $APP/deploy/setup.sh && chown -R www-data:www-data $APP/public $APP/server && systemctl restart directquotes && sleep 1 && curl -fsS http://127.0.0.1:8099/api/health && echo ' deployed'"
