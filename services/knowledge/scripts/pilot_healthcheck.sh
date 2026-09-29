#!/usr/bin/env bash
# pilot_healthcheck.sh - hourly guard against the slow, silent pilot-killers that
# the 5xx log-watcher can't see:
#   1. disk filling up (uploads + ChromaDB + backups + logs) -> everything dies,
#   2. the nightly backup silently failing / not running (false safety),
#   3. the app being down even though the box is up (no logs to scan).
# Collects every problem found and sends ONE alert. Run from /opt/vedomo.
#
# Tunables (env):
#   HEALTH_DISK_PCT          alert when disk >= this %, default 85
#   HEALTH_BACKUP_DIR        where backups land, default <repo>/backups
#   HEALTH_BACKUP_MAX_AGE_H  alert if newest backup older than this, default 26
#   HEALTH_URL               health endpoint; auto-derived from DOMAIN in .env
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/notify.sh
. "$SCRIPT_DIR/notify.sh"

DISK_THRESHOLD="${HEALTH_DISK_PCT:-85}"
BACKUP_DIR="${HEALTH_BACKUP_DIR:-$ROOT_DIR/backups}"
BACKUP_MAX_AGE_H="${HEALTH_BACKUP_MAX_AGE_H:-26}"
BACKUP_TIMEOUT_S="${HEALTH_BACKUP_TIMEOUT_S:-60}"
HEALTH_URL="${HEALTH_URL:-}"

if ! [[ "$DISK_THRESHOLD" =~ ^[1-9][0-9]*$ && "$BACKUP_MAX_AGE_H" =~ ^[1-9][0-9]*$ && "$BACKUP_TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] || [ "$DISK_THRESHOLD" -gt 100 ]; then
  bm_notify "⚠️ Ведомо: неверные пороги healthcheck."
  exit 1
fi

problems=()
backup_status="нет бэкапов"
health_status="не проверялся"

# 1. Disk usage of the filesystem holding the deploy.
disk_pct=""
if disk_output="$(df --output=pcent "$ROOT_DIR" 2>/dev/null)"; then
  disk_pct="$(printf '%s\n' "$disk_output" | tail -1 | tr -dc '0-9')"
fi
if ! [[ "$disk_pct" =~ ^[0-9]+$ ]] || [ "$disk_pct" -gt 100 ]; then
  problems+=("💾 Не удалось проверить заполнение диска.")
elif [ "$disk_pct" -ge "$DISK_THRESHOLD" ]; then
  problems+=("💾 Диск занят на ${disk_pct}% (порог ${DISK_THRESHOLD}%).")
fi

# 2. Validate a published set under the SAME lock as backup/restore/rotation.
# A recent partial archive, log or unrelated file cannot refresh this check.
if age_h="$(timeout --kill-after=5 "$BACKUP_TIMEOUT_S" flock -n "$ROOT_DIR/.backup.lock" python3 "$SCRIPT_DIR/backup_artifacts.py" status "$BACKUP_DIR" "$BACKUP_MAX_AGE_H" 2>/dev/null)"; then
  backup_status="проверен, ${age_h} ч назад"
else
  problems+=("🗄️ Свежий целый бэкап не подтверждён: отсутствует, устарел, повреждён, превышен timeout или проверка занята backup/restore.")
fi

# 3. App health. Use HEALTH_URL if set, else derive https://<DOMAIN>/api/health.
if [ -z "$HEALTH_URL" ] && [ -f "$ROOT_DIR/.env" ]; then
  # Strip surrounding quotes (\042 ") / (\047 ') and any whitespace.
  domain="$(grep -m 1 -E '^DOMAIN=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\042\047[:space:]' || true)"
  [ -n "$domain" ] && HEALTH_URL="https://$domain/api/health"
fi
if [ -n "$HEALTH_URL" ]; then
  if health_body="$(curl -fsS --max-time 12 "$HEALTH_URL" 2>/dev/null)" &&
      printf '%s' "$health_body" | python3 -c 'import json,sys; value=json.load(sys.stdin); sys.exit(0 if isinstance(value,dict) and value.get("status") == "ok" else 1)' >/dev/null 2>&1; then
    health_status="ok"
  else
    health_status="НЕ отвечает"
    problems+=("🚑 /api/health недоступен или вернул неверный ответ.")
  fi
else
  problems+=("🚑 HEALTH_URL/DOMAIN не задан: доступность приложения не проверена.")
fi

host="$(hostname)"
if [ "${#problems[@]}" -eq 0 ]; then
  # Healthy. Cron stays silent; on-demand (HEALTH_ALWAYS_REPORT=1) reports the OK.
  if [ "${HEALTH_ALWAYS_REPORT:-}" = "1" ]; then
    bm_notify "✅ Ведомо health на ${host}:
- 💾 диск: ${disk_pct:-?}%
- 🗄️ бэкап: ${backup_status}
- 🚑 /api/health: ${health_status}"
  fi
  exit 0
fi

text="⚠️ Ведомо health на ${host}:"
for p in "${problems[@]}"; do
  text+=$'\n'"- ${p}"
done
bm_notify "$text"
