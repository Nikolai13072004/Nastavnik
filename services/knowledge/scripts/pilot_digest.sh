#!/usr/bin/env bash
# pilot_digest.sh - once-a-day pulse of the pilot pushed to Telegram, so you see
# movement without logging into the admin screen. Counts the last 24h of activity
# (from usage_events, which carries created_at) plus running totals and the 5xx
# count from the logs. Run from /opt/vedomo, e.g. daily at 21:00.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/notify.sh
. "$SCRIPT_DIR/notify.sh"

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

read -r -d '' SQL <<'EOSQL' || true
select
  (select count(*) from usage_events where action='chat'    and created_at > now() - interval '24 hours'),
  (select count(*) from usage_events where action='summary' and created_at > now() - interval '24 hours'),
  (select count(*) from usage_events where action='study'   and created_at > now() - interval '24 hours'),
  (select count(*) from usage_events where action='upload'  and created_at > now() - interval '24 hours'),
  (select count(distinct user_id) from usage_events where user_id is not null and created_at > now() - interval '24 hours'),
  (select count(*) from users),
  (select count(*) from documents),
  (select count(*) from workspaces where kind='course');
EOSQL

if ! row="$(docker compose $COMPOSE_FILES exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F" " -c "$1"' sh "$SQL" 2>/dev/null)"; then
  bm_notify "📊 Ведомо: не удалось снять метрики (db не ответила)."
  exit 1
fi

if ! [[ "$row" =~ ^[0-9]+(\ [0-9]+){7}$ ]]; then
  bm_notify "📊 Ведомо: не удалось снять метрики (db не ответила)."
  exit 1
fi

read -r chats summaries study uploads active users docs courses <<<"$row"

# 5xx over the last day, straight from the request log.
if ! logs="$(docker compose $COMPOSE_FILES logs --since 24h --no-color backend 2>/dev/null)"; then
  bm_notify "📊 Ведомо: логи недоступны, число ошибок неизвестно."
  exit 1
fi
err24="$(printf '%s\n' "$logs" | grep -Ec 'vedomo\.request .* -> 5[0-9]{2} ' || true)"

bm_notify "📊 Ведомо за сутки ($(hostname)):
Активных пользователей: ${active}
Вопросов: ${chats} | Конспектов: ${summaries} | Тренажёр: ${study} | Загрузок: ${uploads}
Ошибок 5xx: ${err24:-0}
———
Всего: пользователей ${users}, материалов ${docs}, курсов ${courses}"
