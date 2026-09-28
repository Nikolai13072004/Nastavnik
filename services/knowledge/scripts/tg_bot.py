#!/usr/bin/env python3
"""Telegram command bot for Vedomo ops (on-demand *pull*, complementing the
cron *push* alerts). You type a command (or tap a button) and it answers with
live numbers.

Design goals: zero pip installs (stdlib only - urllib/json/subprocess), single
source of truth (it just runs the existing scripts in BM_NOTIFY_STDOUT mode and
relays their text), and owner-locked (it only ever answers ALERT_TELEGRAM_CHAT_ID,
so a stranger who finds the bot gets nothing).

Run under systemd: scripts/vedomo-tgbot.service. Env from /opt/vedomo/.alert.env:
  ALERT_TELEGRAM_BOT_TOKEN   bot token
  ALERT_TELEGRAM_CHAT_ID     owner chat id (the only chat it talks to)
  VEDOMO_DIR                 repo dir, default /opt/vedomo
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

TOKEN = os.environ.get("ALERT_TELEGRAM_BOT_TOKEN", "")
OWNER = str(os.environ.get("ALERT_TELEGRAM_CHAT_ID", ""))
ROOT = os.environ.get("VEDOMO_DIR", "/opt/vedomo")
API = f"https://api.telegram.org/bot{TOKEN}"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"]

# Tap-buttons shown under the chat.
BUTTONS = [["📊 Метрики", "💚 Здоровье"], ["⚠️ Ошибки", "❓ Помощь"]]

HELP = (
    "Ведомо ops-бот. Команды (или жми кнопки):\n"
    "/metrics - пульс за сутки\n"
    "/health - диск, бэкап, /api/health\n"
    "/errors - ошибки 5xx за сутки"
)


def _api(method: str, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    # nosec B310 - URL is a fixed https Telegram API endpoint; ``method`` is an
    # internal literal (getUpdates/sendMessage/...), never user-controlled, so
    # there is no file:// / custom-scheme exposure here.
    with urllib.request.urlopen(req, timeout=70) as resp:  # nosec B310
        return json.load(resp)


def send(text: str) -> None:
    try:
        _api("sendMessage", chat_id=OWNER, text=text,
             reply_markup=json.dumps({"keyboard": BUTTONS, "resize_keyboard": True}))
    except Exception as exc:  # noqa: BLE001
        print(f"send failed: {type(exc).__name__}", file=sys.stderr)


def _run_script(script: str, extra_env: dict) -> str:
    env = dict(os.environ, BM_NOTIFY_STDOUT="1", **extra_env)
    try:
        out = subprocess.run(
            ["bash", f"scripts/{script}"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
        )
        return out.stdout.strip() or out.stderr.strip() or "(пусто)"
    except Exception as exc:  # noqa: BLE001
        return f"Ошибка запуска {script}: {exc}"


def _errors_24h() -> str:
    return _run_script("alert_5xx.sh", {"ALERT_WINDOW": "24h", "ALERT_ALWAYS_REPORT": "1"})


def dispatch(text: str):
    """Map a /command or a button label to a reply, or None to ignore."""
    key = text.strip().lower().lstrip("/")
    if key in ("metrics", "метрики", "📊 метрики"):
        return _run_script("pilot_digest.sh", {})
    if key in ("health", "здоровье", "💚 здоровье"):
        return _run_script("pilot_healthcheck.sh", {"HEALTH_ALWAYS_REPORT": "1"})
    if key in ("errors", "ошибки", "⚠️ ошибки"):
        return _errors_24h()
    if key in ("start", "help", "помощь", "❓ помощь"):
        return HELP
    return None


SLOW_KEYS = {"metrics", "метрики", "📊 метрики", "errors", "ошибки", "⚠️ ошибки"}


def handle(text: str) -> None:
    """Run one command and reply. Runs in its own thread so a slow command (a 24h
    log scan) never blocks the poll loop or the other commands."""
    if text.strip().lower().lstrip("/") in SLOW_KEYS:
        send("⏳ собираю…")
    reply = dispatch(text)
    if reply is not None:
        send(reply)


def _set_menu() -> None:
    cmds = [
        {"command": "metrics", "description": "Пульс за сутки"},
        {"command": "health", "description": "Диск, бэкап, health"},
        {"command": "errors", "description": "Ошибки 5xx за сутки"},
        {"command": "help", "description": "Список команд"},
    ]
    try:
        _api("setMyCommands", commands=json.dumps(cmds))
    except Exception as exc:  # noqa: BLE001
        print(f"setMyCommands failed: {type(exc).__name__}", file=sys.stderr)


def main() -> None:
    if not TOKEN or not OWNER:
        sys.exit("ALERT_TELEGRAM_BOT_TOKEN / ALERT_TELEGRAM_CHAT_ID not set")
    _set_menu()
    # Drain backlog so a restart doesn't replay old commands.
    offset = 0
    try:
        drained = _api("getUpdates", timeout=0)
        if drained.get("result"):
            offset = drained["result"][-1]["update_id"] + 1
    except Exception:  # noqa: BLE001
        pass

    while True:
        try:
            upd = _api("getUpdates", timeout=30, offset=offset)
        except Exception as exc:  # noqa: BLE001
            print(f"getUpdates failed: {type(exc).__name__}", file=sys.stderr)
            time.sleep(5)
            continue
        for item in upd.get("result", []):
            offset = item["update_id"] + 1
            msg = item.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text", "")
            if not text or chat != OWNER:
                continue  # ignore empty messages and anyone but the owner
            # Each command in its own thread: a slow log scan can't freeze the bot.
            threading.Thread(target=handle, args=(text,), daemon=True).start()


if __name__ == "__main__":
    main()
