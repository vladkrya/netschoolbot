#!/usr/bin/env bash
# Серверная часть выкладки: bash -s -- <commit>. Запускают её двое — ручной
# deploy/deploy.sh по SSH и deploy/autodeploy.sh на самом сервере; обоим она
# достаётся из выкладываемого коммита.
#
# Порядок: копия базы → новый код и зависимости → перезапуск → проверка
# /health. Если что-то упало после переключения кода — откат на прежний
# коммит, а если миграция успела изменить схему — и база из копии: прежний
# код с новой схемой не стартует.
set -Eeuo pipefail

TARGET="${1:?нужен коммит}"
APP=/opt/netschoolbot
PY="$APP/venv/bin/python"
cd "$APP"

# Ручная и автоматическая выкладки не должны идти разом: две поверх одного
# каталога — это git merge под ногами у отката.
exec 9>/run/lock/netschoolbot-deploy.lock
flock -w 900 9 || { echo "Другая выкладка не закончилась за 15 минут"; exit 1; }

PREV="$(git rev-parse HEAD)"
STEP="подготовка"
SWITCHED=0
BACKUP=""
NGINX_CONFIGS=()
NGINX_BACKUPS=()

DB="$APP/data/netschoolbot.sqlite3"

step() { STEP="$1"; echo "▶ $1"; }

# Копия и восстановление — встроенным Python без кода приложения: на сервере
# может стоять версия, где инструмента резервных копий ещё нет.
db_tool() {
  "$PY" - "$@" <<'PY'
import sqlite3, sys
action, *paths = sys.argv[1:]
def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
if action == "schema":
    print(ro(paths[0]).execute("PRAGMA user_version").fetchone()[0])
elif action in ("copy", "restore"):
    src, dst = paths
    source = ro(src)
    if action == "restore":
        assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "копия повреждена"
    target = sqlite3.connect(dst)
    source.backup(target)
    if action == "copy":
        # Иначе копия в WAL и каждое открытие оставляет пустые -wal/-shm.
        target.execute("PRAGMA journal_mode = DELETE")
    target.close()
    if action == "copy":
        check = ro(dst)
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "копия повреждена"
        print(dst, check.execute("SELECT COUNT(*) FROM users").fetchone()[0], "пользователей")
PY
}
notify() { "$PY" -m app.tools.notify_admin "$1" >/dev/null 2>&1 || true; }

health() {
  for _ in $(seq 1 15); do
    sleep 2
    systemctl is-active --quiet netschoolbot || continue
    "$PY" - <<'PY' && return 0
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:8283/health", timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
PY
  done
  return 1
}

install_units() {
  install -m 644 deploy/systemd/netschoolbot.service /etc/systemd/system/
  if [ -f deploy/systemd/netschoolbot-redirect.service ]; then
    install -m 644 deploy/systemd/netschoolbot-redirect.service /etc/systemd/system/
  fi
  if [ -f deploy/systemd/netschoolbot-backup.timer ]; then
    install -m 644 deploy/systemd/netschoolbot-backup.service deploy/systemd/netschoolbot-backup.timer /etc/systemd/system/
  fi
  if [ -f deploy/systemd/netschoolbot-autodeploy.timer ]; then
    install -m 644 deploy/systemd/netschoolbot-autodeploy.service deploy/systemd/netschoolbot-autodeploy.timer /etc/systemd/system/
  fi
  systemctl daemon-reload
}

install_nginx() {
  local domain enabled config backup
  # Переменная $netschool_connection (Upgrade только для WebSocket) — общая
  # для всех сайтов. При откате файл остаётся: старым конфигам он не мешает.
  install -m 644 deploy/nginx/netschool-upgrade.conf /etc/nginx/conf.d/netschool-upgrade.conf
  # Обновляем все установленные адреса: Upgrade нужен каждому прокси чата.
  for domain in netschool.ikrya.ru ns.ikrya.ru ns2.ikrya.ru yavds.ikrya.ru; do
    enabled="/etc/nginx/sites-enabled/$domain"
    [ -e "$enabled" ] || continue
    config="$(readlink -f "$enabled")"
    backup="${config}.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$config" "$backup"
    NGINX_CONFIGS+=("$config")
    NGINX_BACKUPS+=("$backup")
    install -m 644 "deploy/nginx/$domain.conf" "$config"
  done
  nginx -t
  systemctl reload nginx
}

install_deps() {
  if [ -f constraints.txt ]; then
    "$APP/venv/bin/pip" install -q -r requirements.txt -c constraints.txt
  else
    "$APP/venv/bin/pip" install -q -r requirements.txt
  fi
}

rollback() {
  local code=$?
  trap - ERR
  local log
  log="$(journalctl -u netschoolbot -n 25 --no-pager 2>/dev/null | tail -25 || true)"
  echo "✖ Шаг «$STEP» не удался (код $code)"
  local index
  for index in "${!NGINX_BACKUPS[@]}"; do
    cp -p "${NGINX_BACKUPS[$index]}" "${NGINX_CONFIGS[$index]}"
  done
  if [ "${#NGINX_BACKUPS[@]}" -gt 0 ]; then
    nginx -t && systemctl reload nginx || true
  fi
  if [ "$SWITCHED" = 1 ]; then
    echo "↩ Откат на $PREV"
    systemctl stop netschoolbot || true
    git reset -q --hard "$PREV"
    if [ -n "$BACKUP" ] && [ "$(db_tool schema "$DB" || echo x)" != "$(db_tool schema "$BACKUP" || echo y)" ]; then
      echo "↩ Схема базы изменилась — возвращаю копию $BACKUP"
      rm -f "$DB-wal" "$DB-shm"
      db_tool restore "$BACKUP" "$DB" || echo "!! База не восстановлена — нужна ручная проверка"
    fi
    install_deps || true
    install_units || true
    systemctl start netschoolbot || true
    if health; then ROLLED="откат на ${PREV:0:7} прошёл, бот работает"; else ROLLED="ОТКАТ ТОЖЕ НЕ ПОДНЯЛСЯ — нужен ручной вход"; fi
  else
    ROLLED="код на сервере не менялся"
  fi
  notify "❌ Выкладка ${TARGET:0:7} упала на шаге «$STEP»: $ROLLED.

$log"
  echo "$ROLLED"
  exit 1
}
trap rollback ERR

step "Получение кода"
git fetch -q origin main
# Коммит обязан быть в main, но не обязан быть его вершиной: автодеплой
# выкатывает проверенный CI коммит, а main к этому времени может уйти дальше.
git merge-base --is-ancestor "$TARGET" origin/main || { echo "$TARGET нет в origin/main"; false; }
if [ "$PREV" = "$TARGET" ]; then echo "Уже выложено: $TARGET"; exit 0; fi

step "Резервная копия базы"
mkdir -p "$APP/data/backups"
BACKUP="$APP/data/backups/netschoolbot-predeploy-$(date +%Y%m%d-%H%M%S).sqlite3"
db_tool copy "$DB" "$BACKUP"
# Копии перед выкладкой копятся отдельно от ежедневных: оставляем пять последних.
ls -1t "$APP"/data/backups/netschoolbot-predeploy-*.sqlite3 2>/dev/null | tail -n +6 | xargs -r rm -f
for extra in "$APP"/data/backups/*.sqlite3-wal "$APP"/data/backups/*.sqlite3-shm; do
  [ -e "$extra" ] && [ ! -e "${extra%-*}" ] && rm -f "$extra"
done
# Копии конфигов nginx: по пять последних на сайт.
for domain in netschool.ikrya.ru ns.ikrya.ru ns2.ikrya.ru yavds.ikrya.ru; do
  ls -1t /etc/nginx/sites-available/"$domain".bak-* 2>/dev/null | tail -n +6 | xargs -r rm -f
done

step "Переключение на ${TARGET:0:7}"
SWITCHED=1
git merge -q --ff-only "$TARGET"

step "Зависимости"
install_deps

step "Юниты systemd"
install_units
# Копия до установки таймера снималась вручную — теперь по расписанию.
systemctl enable --now netschoolbot-backup.timer >/dev/null 2>&1 || true
systemctl enable --now netschoolbot-autodeploy.timer >/dev/null 2>&1 || true

step "Конфигурация nginx"
install_nginx

step "Перезапуск"
systemctl restart netschoolbot

step "Проверка /health"
health

trap - ERR
echo "✔ Выложено ${TARGET:0:7} ($(git log -1 --format=%s))"
notify "✅ Выложено ${TARGET:0:7}: $(git log -1 --format=%s)"
