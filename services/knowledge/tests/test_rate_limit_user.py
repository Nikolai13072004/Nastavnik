"""Stage 13-1: per-user rate-limit keying + live-session ban enforcement.

The key-function tests are pure unit tests on ``rate_limit.user_or_ip`` — that
proves *our* keying logic (per-user vs per-IP, cookie==Bearer, safe fallback);
that distinct keys land in distinct buckets is slowapi's own behaviour. The ban
test proves a deactivated user's live cookie is rejected on the next request.
"""

from __future__ import annotations

from types import SimpleNamespace

import config
from src import rate_limit
from src.db import SessionLocal
from src.db_models import User
from src.security import create_access_token


def _fake_request(*, cookie=None, bearer=None, host="10.0.0.1"):
    headers = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    cookies = {}
    if cookie:
        cookies[config.AUTH_COOKIE_NAME] = cookie
    return SimpleNamespace(
        cookies=cookies,
        headers=headers,
        client=SimpleNamespace(host=host),
    )


# ---------------------------------------------------------------------------
# Key function
# ---------------------------------------------------------------------------


def test_key_is_user_for_cookie_token():
    token = create_access_token("user-123")
    assert rate_limit.user_or_ip(_fake_request(cookie=token)) == "user:user-123"


def test_key_is_user_for_bearer_token():
    token = create_access_token("user-123")
    assert rate_limit.user_or_ip(_fake_request(bearer=token)) == "user:user-123"


def test_cookie_and_bearer_give_the_same_user_key():
    token = create_access_token("user-xyz")
    via_cookie = rate_limit.user_or_ip(_fake_request(cookie=token))
    via_bearer = rate_limit.user_or_ip(_fake_request(bearer=token))
    assert via_cookie == via_bearer == "user:user-xyz"


def test_distinct_users_get_distinct_keys_from_same_ip():
    t1 = create_access_token("u1")
    t2 = create_access_token("u2")
    k1 = rate_limit.user_or_ip(_fake_request(cookie=t1, host="1.1.1.1"))
    k2 = rate_limit.user_or_ip(_fake_request(cookie=t2, host="1.1.1.1"))
    assert (k1, k2) == ("user:u1", "user:u2")
    assert k1 != k2  # same NAT/IP, but separate buckets — the whole point


def test_anonymous_falls_back_to_ip():
    assert rate_limit.user_or_ip(_fake_request(host="9.9.9.9")) == "ip:9.9.9.9"


def test_malformed_token_falls_back_to_ip():
    assert rate_limit.user_or_ip(_fake_request(cookie="not-a-jwt", host="9.9.9.9")) == "ip:9.9.9.9"


def test_expired_token_falls_back_to_ip():
    expired = create_access_token("u1", expires_minutes=-1)
    assert rate_limit.user_or_ip(_fake_request(cookie=expired, host="9.9.9.9")) == "ip:9.9.9.9"


# ---------------------------------------------------------------------------
# Live-session ban
# ---------------------------------------------------------------------------


def test_deactivated_user_is_rejected_on_live_session(authed_client):
    """A ban (is_active=False) rejects the already-issued cookie on the next
    request — not only at login."""
    # Sanity: the live session works before the ban.
    assert authed_client.get("/api/auth/me").status_code == 200

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "tester@example.com").one()
        user.is_active = False
        db.commit()
    finally:
        db.close()

    resp = authed_client.get("/api/auth/me")
    assert resp.status_code == 401


def test_token_issued_before_tokens_valid_after_is_revoked(authed_client):
    """A JWT older than the user's tokens_valid_after is rejected — this is what
    forces a fresh login after a ban (Stage 13)."""
    from datetime import datetime, timedelta, timezone

    assert authed_client.get("/api/auth/me").status_code == 200

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "tester@example.com").one()
        # Revoke everything issued before "now + 1h" → the current token is older.
        user.tokens_valid_after = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    assert authed_client.get("/api/auth/me").status_code == 401
