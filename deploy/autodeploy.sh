#!/usr/bin/env bash
# Автодеплой на сервере: раз в минуту (netschoolbot-autodeploy.timer)
# выкатывает ветку `deploy` из GitHub (vladkrya/netschoolbot).
#
# Ветку двигает .github/workflows/ci.yml и только после зелёных тестов,
# поэтому здесь тестов нет. Токенов GitHub на сервере тоже нет: origin
# читается deploy key-ем только на чтение (`Host github-netschoolbot` в
# /root/.ssh/config).
#
# Выкатывается только движение вперёд — новая ревизия обязана быть потомком
# текущего HEAD. Прод новее ветки (ручной deploy.sh, CI ещё идёт) — ждём;
# история разошлась — одно уведомление и ничего не трогаем. Упавшая и
# откаченная ревизия повторно не выкатывается: иначе бот перезапускался бы
# каждую минуту.
#
# Саму выкладку (копия базы, зависимости, юниты, nginx, /health, откат)
# делает deploy/remote-deploy.sh — из того коммита, который выкладывается,
# как и при ручном deploy.sh.
set -Eeuo pipefail

APP="${APP:-/opt/netschoolbot}"
BRANCH="${AUTODEPLOY_BRANCH:-deploy}"
STATE="${AUTODEPLOY_STATE:-/var/lib/netschoolbot-autodeploy}"

notify() { "$APP/venv/bin/python" -m app.tools.notify_admin "$1" >/dev/null 2>&1 || true; }
notify_once() {
  [ "$(cat "$STATE/notified" 2>/dev/null || true)" = "$1" ] && return 0
  notify "$2"
  echo "$1" > "$STATE/notified"
}

mkdir -p "$STATE"
cd "$APP"
git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"

NEW="$(git rev-parse "refs/remotes/origin/$BRANCH")"
CUR="$(git rev-parse HEAD)"
[ "$NEW" = "$CUR" ] && exit 0
[ "$NEW" = "$(cat "$STATE/failed" 2>/dev/null || true)" ] && exit 0

if ! git merge-base --is-ancestor "$CUR" "$NEW"; then
  if git merge-base --is-ancestor "$NEW" "$CUR"; then
    echo "на проде ${CUR:0:7} новее $BRANCH ${NEW:0:7} — жду CI"
    exit 0
  fi
  echo "${NEW:0:7} не потомок ${CUR:0:7} — пропускаю"
  notify_once "$NEW" "⚠️ Автодеплой: ${NEW:0:7} в ветке $BRANCH не потомок выложенного ${CUR:0:7}. Ничего не выкладываю — проверьте историю или выложите deploy/deploy.sh."
  exit 0
fi

echo "▶ автодеплой ${NEW:0:7}: $(git log -1 --format=%s "$NEW")"
# Успех и провал remote-deploy.sh сообщает админу сам.
if ! git show "$NEW:deploy/remote-deploy.sh" | bash -s -- "$NEW"; then
  echo "$NEW" > "$STATE/failed"
  exit 1
fi
