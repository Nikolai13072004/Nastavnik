"""Stage 41: fail-fast on the public dev JWT secret in production."""

import pytest

import config


def test_validate_config_flags_default_jwt_secret_in_prod(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET_KEY", config.INSECURE_JWT_DEFAULT)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)  # prod-like signal

    errors = config.validate_config()
    assert any("JWT_SECRET_KEY" in e for e in errors)
    with pytest.raises(RuntimeError):
        config.assert_jwt_secret_safe()


def test_custom_jwt_secret_ok_in_prod(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "a-real-unique-production-secret")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)

    assert not any("JWT_SECRET_KEY" in e for e in config.validate_config())
    config.assert_jwt_secret_safe()  # must not raise


def test_default_secret_ok_in_dev(monkeypatch):
    # No production signal: the dev default is allowed (local dev / CI).
    monkeypatch.setattr(config, "JWT_SECRET_KEY", config.INSECURE_JWT_DEFAULT)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("APP_ENV", "")

    assert not any("JWT_SECRET_KEY" in e for e in config.validate_config())
    config.assert_jwt_secret_safe()  # must not raise
