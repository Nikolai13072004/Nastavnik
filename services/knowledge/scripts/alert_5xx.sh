#!/usr/bin/env bash
# alert_5xx.sh - cron watchdog for the pilot. If the backend logged any 5xx
# responses or email-delivery failures in the last window, send an alert.
# Stateless: repeated runs can repeat alerts; this is not incident deduplication.
#
# Stateless; secrets come from the environment, NEVER hardcoded / committed.
#
#   ALERT_WINDOW                  log look-back (docker --since), default 10m
#   ALERT_5XX_THRESHOLD           min 5xx count to alert, default 1
#   ALERT_MAIL_THRESHOLD          min email failure count, default 1
#   ALERT_TELEGRAM_BOT_TOKEN +    -> Telegram (no SMTP needed)
#   ALERT_TELEGRAM_CHAT_ID
#   ALERT_WEBHOOK_URL             -> generic POST {"text": ...} (Slack/Discord/custom)
#   BM_NOTIFY_DRY_RUN=1           -> explicit local test, not delivery
#   (none set)                    -> delivery failure
#
# Relies on the request log line from src/obs.py:
#   vedomo.request <METHOD> <path> -> <status> <dur>ms ip=<ip> rid=<rid>
#
# Cron example (every 10 min), reading secrets from a root-only env file:
#   */10 * * * * cd /opt/vedomo && set -a && . /opt/vedomo/.alert.env && set +a && bash scripts/alert_5xx.sh >> /var/log/vedomo-alert.log 2>&1
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/notify.sh
. "$SCRIPT_DIR/notify.sh"

WINDOW="${ALERT_WINDOW:-10m}"
THRESHOLD="${ALERT_5XX_THRESHOLD:-1}"
MAIL_THRESHOLD="${ALERT_MAIL_THRESHOLD:-1}"
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

if ! [[ "$THRESHOLD" =~ ^[1-9][0-9]*$ && "$MAIL_THRESHOLD" =~ ^[1-9][0-9]*$ ]]; then
  bm_notify "⚠️ Ведомо: неверные пороги мониторинга ошибок."
  exit 1
fi

# Recent backend logs, keep only 5xx request lines.
if ! logs="$(docker compose $COMPOSE_FILES logs --since "$WINDOW" --no-color backend 2>/dev/null)"; then
  bm_notify "⚠️ Ведомо: не удалось прочитать backend-логи; состояние ошибок неизвестно."
  exit 1
fi
hits="$(printf '%s\n' "$logs" | grep -E 'vedomo\.request .* -> 5[0-9]{2} ' || true)"
count="$(printf '%s' "$hits" | grep -c . || true)"
mail_count="$(printf '%s\n' "$logs" | grep -c 'email_delivery_failed' || true)"

if [ "${count:-0}" -lt "$THRESHOLD" ] && [ "${mail_count:-0}" -lt "$MAIL_THRESHOLD" ]; then
  if [ "${ALERT_ALWAYS_REPORT:-}" = "1" ]; then
    bm_notify "Ведомо за ${WINDOW}: 5xx — ${count}; ошибки отправки почты — ${mail_count}. Пороги не превышены."
  fi
  exit 0
fi

host="$(hostname)"
# Do not forward raw paths, IP addresses or exception content to a third party.
bm_notify "⚠️ Ведомо за ${WINDOW} на ${host}: 5xx — ${count}; ошибки отправки почты — ${mail_count}.
Подробности проверьте в локальных логах."
