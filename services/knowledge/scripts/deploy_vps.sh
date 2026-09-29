#!/usr/bin/env bash
# Vedomo / Vedomo - one-shot VPS deploy (Stage 46).
#
# Idempotent. Run as root on a fresh Ubuntu 24.04 box:
#
#   export API_KEY='<your-openrouter-key>'
#   curl -fsSL https://raw.githubusercontent.com/clapzy2/vedomo-pro/stage-46-security-hardening/scripts/deploy_vps.sh -o /root/deploy.sh
#   bash /root/deploy.sh
#
# Installs Docker, clones the repo, writes a production .env (auto-generating the
# JWT + Postgres secrets, keeping API_KEY from the environment), then builds and
# starts the Postgres + backend + frontend stack in the background. The backend
# entrypoint runs `alembic upgrade head` itself, and the embedding/reranker
# models (~2 GB) download from HuggingFace on the first request.
set -euo pipefail

REPO_URL="https://github.com/clapzy2/vedomo-pro"
BRANCH="${BRANCH:-stage-46-security-hardening}"
APP_DIR="/opt/vedomo"
SERVER_IP="${SERVER_IP:-$(hostname -I | awk '{print $1}')}"

echo "================================================================"
echo " Vedomo deploy  |  IP=${SERVER_IP}  branch=${BRANCH}"
echo "================================================================"

# --- 1. Docker ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
echo "==> $(docker --version)"
echo "==> $(docker compose version | head -1)"

# --- 2. Code -----------------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  echo "==> Updating existing checkout in $APP_DIR"
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  echo "==> Cloning into $APP_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# --- 3. .env (secrets preserved across re-runs) ------------------------------
if [ ! -f .env ]; then
  echo "==> Writing .env"
  if [ -z "${API_KEY:-}" ]; then
    echo "!!  API_KEY не задан - чат/конспекты не заработают, пока не впишешь ключ в .env."
    echo "!!  Лучше повтори так:  export API_KEY='твой-openrouter-ключ'  затем снова запусти скрипт."
  fi
  JWT="$(openssl rand -hex 48)"
  PGPW="$(openssl rand -hex 16)"
  cat > .env <<ENV
LLM_MODE=api
API_KEY=${API_KEY:-CHANGE_ME}
API_MODEL=qwen/qwen3-32b
POSTGRES_USER=vedomo
POSTGRES_PASSWORD=${PGPW}
POSTGRES_DB=vedomo
JWT_SECRET_KEY=${JWT}
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080
AUTH_COOKIE_SECURE=false
APP_BASE_URL=http://${SERVER_IP}:3000
EMAIL_BACKEND=console
EMAIL_FROM=Ведомо <no-reply@vedomo.local>
ENV
else
  echo "==> .env уже есть, не трогаю (секреты и API_KEY сохранены)"
fi

# --- 4. Build + start (background; survives a console disconnect) -------------
echo "==> Сборка + запуск в фоне (первая сборка ~10-15 мин; модели ~2ГБ качаются при первом запросе)"
nohup docker compose up --build -d > "$APP_DIR/deploy.log" 2>&1 &
sleep 2
echo "================================================================"
echo " Сборка пошла в фоне. Дальше команды для проверки:"
echo "   tail -n 40 $APP_DIR/deploy.log                      # прогресс сборки"
echo "   docker compose -f $APP_DIR/docker-compose.yml ps    # статус контейнеров"
echo ""
echo " Когда backend и frontend в состоянии running ->"
echo "   открой в браузере:  http://${SERVER_IP}:3000"
echo "================================================================"
