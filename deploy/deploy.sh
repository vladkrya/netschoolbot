#!/usr/bin/env bash
# Выкладка: тесты здесь → push в dc09 → на сервере обновление с проверкой и откатом.
#
#     deploy/deploy.sh                 # на root@vdsru.ikrya.ru
#     DEPLOY_HOST=root@other deploy/deploy.sh
#
# GitHub Actions больше нет (аккаунт заблокирован), поэтому выкладка — этим
# скриптом. Непрошедшие тесты останавливают её до того, как что-то уйдёт на сервер.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${DEPLOY_HOST:-root@vdsru.ikrya.ru}"
REMOTE="${DEPLOY_REMOTE:-dc09}"
PYTHON="${PYTHON:-./venv/bin/python}"

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "Выкладывается только main"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Есть незакоммиченные изменения"; exit 1; }

echo "▶ Тесты"
"$PYTHON" -m pytest -q -p no:cacheprovider
"$PYTHON" -c "import app.main, app.web.server, app.bot.app"

TARGET="$(git rev-parse HEAD)"
echo "▶ Отправка $TARGET в $REMOTE"
git push "$REMOTE" main

echo "▶ Выкладка на $HOST"
# Серверная часть берётся из этого же коммита, а не из того, что лежит на сервере.
ssh -o ControlMaster=auto -o ControlPath=/tmp/ns-deploy-%C -o ControlPersist=120 \
  "$HOST" "bash -s -- $TARGET" < deploy/remote-deploy.sh
