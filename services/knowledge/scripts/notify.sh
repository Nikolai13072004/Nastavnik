#!/usr/bin/env bash
# notify.sh - shared alert delivery for the Vedomo watchdogs.
#
# Source it, then call:  bm_notify "your message"
#
# Channel comes from the environment (e.g. root-only /opt/vedomo/.alert.env),
# never hardcoded:
#   ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID  -> Telegram (no SMTP)
#   ALERT_WEBHOOK_URL                                  -> generic POST {"text":...}
#   BM_NOTIFY_DRY_RUN=1                                -> explicit local dry run
#   (none set)                                         -> fail (no delivery)
#
# Pure function library - no `set` here, the calling script owns its options.

bm_notify() {
  local text="$1"
  # On-demand mode (used by the Telegram command bot): print instead of send, so
  # the caller captures the exact same message text and replies with it itself.
  if [ "${BM_NOTIFY_STDOUT:-}" = "1" ]; then
    printf '%s\n' "$text"
    return 0
  fi
  if [ "${BM_NOTIFY_DRY_RUN:-}" = "1" ]; then
    printf 'DRY RUN (not delivered):\n%s\n' "$text" >&2
    return 0
  fi
  local notify_dir
  notify_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # Secrets stay in env, text in stdin: neither is placed in process arguments.
  printf '%s' "$text" | python3 "$notify_dir/notify_delivery.py"
}
