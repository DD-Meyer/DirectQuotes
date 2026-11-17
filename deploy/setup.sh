#!/usr/bin/env bash
# Server setup, safe to re-run. deploy.sh calls it on every deploy.
set -euo pipefail
APP=/var/www/directquotes
mkdir -p $APP/public $APP/server $APP/data
[ -f $APP/.env ] || { cp $APP/.env.example $APP/.env; echo ">> Edit $APP/.env and set SMTP_HOST / SMTP_PASS"; }
chmod 640 $APP/.env; chown root:www-data $APP/.env
chown -R www-data:www-data $APP/data
install -m 644 $APP/deploy/directquotes.service /etc/systemd/system/directquotes.service
systemctl daemon-reload
systemctl enable directquotes >/dev/null 2>&1
