"""Real shell watchdogs, isolated commands/snapshots; never send notifications."""
import os
from pathlib import Path
import subprocess
import time

import pytest

from scripts.backup_artifacts import check_latest
from test_backup_artifacts import snapshot, STAMP


def test_backup_status_ignores_recent_partial(tmp_path):
    (tmp_path / "fresh.sql.gz").write_bytes(b"partial")
    with pytest.raises(ValueError, match="no published"):
        check_latest(tmp_path, 26)


@pytest.mark.parametrize("age", [26 * 3600, -3600])
def test_backup_status_rejects_stale_and_future(tmp_path, age):
    _, _, manifest = snapshot(tmp_path)
    now = time.time()
    os.utime(manifest, (now - age, now - age))
    with pytest.raises(ValueError, match="stale or clock"):
        check_latest(tmp_path, 26, now=now)


def test_backup_status_verifies_contents(tmp_path):
    db, _, _ = snapshot(tmp_path)
    assert check_latest(tmp_path, 26) == 0
    db.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        check_latest(tmp_path, 26)


@pytest.fixture
def watchdog(tmp_path):
    if os.name == "nt":
        pytest.skip("real Bash/flock watchdog gate runs in Linux")
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("notify.sh", "notify_delivery.py", "alert_5xx.sh", "pilot_healthcheck.sh", "pilot_digest.sh", "backup_artifacts.py"):
        (scripts / name).write_text((root / "scripts" / name).read_text())
    bindir = tmp_path / "bin"
    bindir.mkdir()
    commands = {
        "df": '#!/bin/sh\n[ "${FAIL_DISK:-}" != 1 ] || exit 1\nprintf "Use%%\\n 20%%\\n"\n',
        "docker": '#!/bin/sh\ncase "$*" in *"exec -T db"*) [ "${FAIL_DB:-}" != 1 ] || exit 1; printf "%s\\n" "${DB_ROW:-0 0 0 0 0 0 0 0}"; exit 0;; esac\n[ "${FAIL_LOGS:-}" != 1 ] || exit 1\nprintf "%s\\n" "${LOG_TEXT:-}"\n',
        "curl": '#!/bin/sh\n[ "${FAIL_HEALTH:-}" != 1 ] || exit 1\nprintf "%s" "$HEALTH_BODY"\n',
    }
    for name, content in commands.items():
        file = bindir / name
        file.write_text(content)
        file.chmod(0o700)
    backups = tmp_path / "backups"
    backups.mkdir()
    snapshot(backups)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               BM_NOTIFY_STDOUT="1", HEALTH_BACKUP_DIR=str(backups),
               HEALTH_URL="https://synthetic.invalid/api/health", HEALTH_BODY='{"status":"ok"}',
               HEALTH_DISK_PCT="85", HEALTH_BACKUP_MAX_AGE_H="26",
               ALERT_5XX_THRESHOLD="1", ALERT_MAIL_THRESHOLD="1")

    def invoke(script, **overrides):
        return subprocess.run(["bash", str(scripts / script)], env=dict(env, **overrides),
                              capture_output=True, text=True, timeout=15)
    return tmp_path, backups, invoke


def test_healthy_watchdog_is_silent(watchdog):
    _, _, invoke = watchdog
    result = invoke("pilot_healthcheck.sh")
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.parametrize("failure", ["missing", "partial", "corrupt", "stale"])
def test_bad_backup_notifies(watchdog, failure):
    _, backups, invoke = watchdog
    if failure in {"missing", "partial"}:
        for file in backups.iterdir():
            file.unlink()
        if failure == "missing":
            backups.rmdir()
        else:
            (backups / "fresh.sql.gz").write_bytes(b"unfinished")
    elif failure == "corrupt":
        (backups / f"db-{STAMP}.sql.gz").write_bytes(b"broken")
    else:
        old = time.time() - 27 * 3600
        os.utime(backups / f"manifest-{STAMP}.sha256", (old, old))
        (backups / "new-unrelated-file").write_text("not a snapshot")
    result = invoke("pilot_healthcheck.sh")
    assert "Свежий целый бэкап не подтверждён" in result.stdout


@pytest.mark.parametrize("overrides", [
    {"FAIL_DISK": "1"}, {"FAIL_HEALTH": "1"}, {"HEALTH_URL": ""},
    {"HEALTH_BODY": "<html>proxy login</html>"}, {"HEALTH_BODY": '{"status":"error"}'},
])
def test_unknown_health_is_not_success(watchdog, overrides):
    _, _, invoke = watchdog
    result = invoke("pilot_healthcheck.sh", **overrides)
    assert "⚠️" in result.stdout and "✅" not in result.stdout


def test_backup_lock_does_not_report_validated(watchdog):
    import fcntl
    root, _, invoke = watchdog
    with (root / ".backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke("pilot_healthcheck.sh")
    assert "проверка занята" in result.stdout


def test_backup_timeout_reports_unknown(watchdog):
    root, _, invoke = watchdog
    helper = root / "scripts/backup_artifacts.py"
    helper.write_text("import time; time.sleep(3)\n" + helper.read_text())
    result = invoke("pilot_healthcheck.sh", HEALTH_BACKUP_TIMEOUT_S="1")
    assert "превышен timeout" in result.stdout and "✅" not in result.stdout


def test_log_read_failure_notifies_and_fails(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", FAIL_LOGS="1")
    assert result.returncode != 0
    assert "состояние ошибок неизвестно" in result.stdout


def test_mail_and_5xx_counts_do_not_leak_log_content(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", LOG_TEXT=(
        "vedomo.request GET /private-document -> 503 10ms ip=private-ip\n"
        "email_delivery_failed reason=SMTPException private-token\n"
    ))
    assert result.returncode == 0
    assert "5xx — 1" in result.stdout and "почты — 1" in result.stdout
    assert "private-" not in result.stdout


def test_mail_failure_alone_triggers(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", LOG_TEXT="email_delivery_failed reason=TimeoutError")
    assert "почты — 1" in result.stdout


def test_no_errors_is_silent(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", LOG_TEXT="vedomo.request GET /api/health -> 200 1ms")
    assert result.returncode == 0 and result.stdout == ""


def test_on_demand_empty_report(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", ALERT_ALWAYS_REPORT="1")
    assert "5xx — 0" in result.stdout and "почты — 0" in result.stdout


@pytest.mark.parametrize("overrides", [{"FAIL_DB": "1"}, {"FAIL_LOGS": "1"}, {"DB_ROW": "bad data"}])
def test_digest_failure_is_not_zero_metrics(watchdog, overrides):
    _, _, invoke = watchdog
    result = invoke("pilot_digest.sh", **overrides)
    assert result.returncode != 0
    assert (
        "не удалось снять метрики" in result.stdout
        or "число ошибок неизвестно" in result.stdout
    )
    assert "Ошибок 5xx: 0" not in result.stdout


def test_digest_success(watchdog):
    _, _, invoke = watchdog
    result = invoke("pilot_digest.sh", DB_ROW="1 2 3 4 5 6 7 8")
    assert result.returncode == 0 and "Активных пользователей: 5" in result.stdout


def test_missing_domain_in_env_still_reports(watchdog):
    root, _, invoke = watchdog
    (root / ".env").write_text("SYNTHETIC=not-a-secret\n")
    result = invoke("pilot_healthcheck.sh", HEALTH_URL="")
    assert "доступность приложения не проверена" in result.stdout


def test_unconfigured_delivery_fails(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", BM_NOTIFY_STDOUT="0", BM_NOTIFY_DRY_RUN="0",
                    LOG_TEXT="email_delivery_failed", ALERT_TELEGRAM_BOT_TOKEN="",
                    ALERT_TELEGRAM_CHAT_ID="", ALERT_WEBHOOK_URL="")
    assert result.returncode != 0 and "alert_delivery_failed" in result.stderr


def test_explicit_dry_run_does_not_deliver(watchdog):
    _, _, invoke = watchdog
    result = invoke("alert_5xx.sh", BM_NOTIFY_STDOUT="0", BM_NOTIFY_DRY_RUN="1",
                    LOG_TEXT="email_delivery_failed")
    assert result.returncode == 0 and "not delivered" in result.stderr


@pytest.mark.parametrize("script,key", [("alert_5xx.sh", "ALERT_5XX_THRESHOLD"),
                                      ("pilot_healthcheck.sh", "HEALTH_DISK_PCT")])
def test_invalid_threshold_notifies(watchdog, script, key):
    _, _, invoke = watchdog
    result = invoke(script, **{key: "not-a-number"})
    assert result.returncode != 0 and "неверные пороги" in result.stdout
