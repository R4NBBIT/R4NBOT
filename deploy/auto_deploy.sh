#!/usr/bin/env bash
# cron으로 주기적으로 돌리는 자동 배포 스크립트.
# 원격(origin/main)에 새 커밋이 있으면 pull 받고, requirements.txt가 바뀌었으면
# 의존성을 다시 설치하고, systemd 서비스(r4nbot)를 재시작합니다.
# 변경이 없으면 아무것도 안 하고 조용히 끝납니다.
#
# root 권한으로 돌리는 걸 가정합니다 (systemctl restart 때문에).
# git/pip 작업은 레포 소유자(기본값: r4nbot) 권한으로 실행해서 파일 소유권이
# 안 깨지게 합니다.
#
# crontab 등록 예시 (root crontab, `sudo crontab -e`):
#   */2 * * * * /opt/r4nbot/deploy/auto_deploy.sh >> /var/log/r4nbot-deploy.log 2>&1

set -euo pipefail

# ---- 환경에 맞게 수정하세요 ----
REPO_DIR="/opt/r4nbot"
REPO_USER="r4nbot"
SERVICE_NAME="r4nbot"
BRANCH="main"
VENV_PIP="$REPO_DIR/venv/bin/pip"
# --------------------------------

LOCK_FILE="/tmp/${SERVICE_NAME}-auto-deploy.lock"

# 동시에 두 번 돌지 않게 (이전 실행이 아직 안 끝났으면 바로 종료)
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date '+%F %T')] 이미 실행 중인 배포 스크립트가 있어서 건너뜀."
    exit 0
fi

cd "$REPO_DIR"

sudo -u "$REPO_USER" git fetch origin "$BRANCH" --quiet

LOCAL_REV="$(sudo -u "$REPO_USER" git rev-parse HEAD)"
REMOTE_REV="$(sudo -u "$REPO_USER" git rev-parse "origin/$BRANCH")"

if [ "$LOCAL_REV" = "$REMOTE_REV" ]; then
    # 변경 없음 -> 조용히 종료 (로그 안 남김)
    exit 0
fi

echo "[$(date '+%F %T')] 새 커밋 발견: $LOCAL_REV -> $REMOTE_REV"

# requirements.txt가 이번에 바뀌는지 미리 확인
REQS_CHANGED="false"
if ! sudo -u "$REPO_USER" git diff --quiet "$LOCAL_REV" "$REMOTE_REV" -- requirements.txt; then
    REQS_CHANGED="true"
fi

sudo -u "$REPO_USER" git merge --ff-only "origin/$BRANCH"
echo "[$(date '+%F %T')] pull 완료."

if [ "$REQS_CHANGED" = "true" ]; then
    echo "[$(date '+%F %T')] requirements.txt 변경 감지 -> 의존성 재설치."
    sudo -u "$REPO_USER" "$VENV_PIP" install -r requirements.txt
fi

systemctl restart "$SERVICE_NAME"
echo "[$(date '+%F %T')] $SERVICE_NAME 재시작 완료."
