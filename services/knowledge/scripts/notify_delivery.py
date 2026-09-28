"""Bounded alert POST; configuration is operator-owned, never request input."""
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deliver(text: str) -> None:
    token = os.environ.get("ALERT_TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "")
    telegram = bool(token or chat)
    payload = {"text": text[:3500]}
    if telegram:
        if not token or not chat or any(c in token for c in "/?#\r\n"):
            raise ValueError("invalid channel")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload["chat_id"] = chat
    else:
        url = os.environ.get("ALERT_WEBHOOK_URL", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("invalid channel")
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    with opener.open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise ValueError("delivery rejected")
        if telegram:
            result = json.loads(response.read(65537))
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("delivery rejected")


def main() -> int:
    try:
        deliver(sys.stdin.read(3500))
    except Exception as exc:  # Provider errors may include credential-bearing URLs.
        print(f"alert_delivery_failed reason={type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
