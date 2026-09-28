#!/usr/bin/env bash
# Vedomo - backup (Stage 46; consistency + encryption + flock; Stage 54
# hotfix: single point-in-time snapshot).
#
# Produces, in ./backups, ONE consistent snapshot taken with `backend` stopped
# (the only writer to Postgres, vectors and docs), so the DB dump, the vectors
# and the files all reflect the same instant:
#   db-<TS>.sql.gz       Postgres dump (--clean --if-exists, replayable)
#   files-<TS>.tar.gz    ChromaDB vectors (app/data) + uploaded docs (app/docs)
#   manifest-<TS>.sha256 checksums, verified before any restore
#   env-<TS>.age         OPTIONAL, only if BACKUP_SECRETS_RECIPIENT is set:
#                        the .env encrypted to an age public key
#
# Secrets: the plaintext .env (JWT/Postgres/SMTP/API keys) is NEVER written to a
# backup or sent offsite. Keep .env in a password manager - that is the copy you
# restore from. Optionally set BACKUP_SECRETS_RECIPIENT to an `age` recipient
# (public key) for an ENCRYPTED copy whose private key never lives on the VPS.
# The script does not print secret values and does not use `set -x`.
#
# Offsite (strongly recommended - a backup on the same disk dies with the disk):
#   OFFSITE_REMOTE="cryptbackups:" bash scripts/backup.sh
# Use an `rclone crypt` remote so the offsite copy is encrypted at rest.
#
# Consistency: PAUSE_FOR_CONSISTENCY=true (default) stops `backend` for the whole
# dump+archive (~tens of seconds at 03:00). Set it false ONLY if you snapshot all
# volumes another atomically-consistent way (LVM/ZFS) - then dump+tar run live.
#
# Override the compose invocation (e.g. for an isolated DR drill):
#   COMPOSE="docker compose -p bm_drill -f docker-compose.yml" bash scripts/backup.sh
#
# Daily 03:00 (crontab -e):
#   0 3 * * * OFFSITE_REMOTE="cryptbackups:" /opt/vedomo/scripts/backup.sh >> /opt/vedomo/backups/backup.log 2>&1
#
# Restore: scripts/restore.sh + design/backup-and-restore.md.
set -euo pipefail
umask 077

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

DEST="${BACKUP_DIR:-$APP_DIR/backups}"
RETENTION="${RETENTION:-14}"
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"
PAUSE_FOR_CONSISTENCY="${PAUSE_FOR_CONSISTENCY:-true}"
HELPER_IMAGE="${HELPER_IMAGE:-alpine:3}"
BACKUP_SECRETS_RECIPIENT="${BACKUP_SECRETS_RECIPIENT:-}"
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
[[ "$RETENTION" =~ ^[1-9][0-9]*$ ]] || { echo "FATAL: RETENTION must be positive" >&2; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: Python 3.11+ is required" >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
command -v flock >/dev/null || { echo "FATAL: flock is required" >&2; exit 1; }
if [ -n "$OFFSITE_REMOTE" ]; then
  command -v rclone >/dev/null || { echo "FATAL: requested offsite copy requires rclone" >&2; exit 1; }
fi
if [ -n "$BACKUP_SECRETS_RECIPIENT" ]; then
  command -v age >/dev/null || { echo "FATAL: requested secret backup requires age" >&2; exit 1; }
  [ -f "$APP_DIR/.env" ] || { echo "FATAL: requested secret backup has no .env" >&2; exit 1; }
fi

# --- single-run guard (flock): two overlapping cron runs would race on the
# backend stop/start and on rotation. Non-blocking: a second run just exits.
exec 9>"$APP_DIR/.backup.lock"
flock -n 9 || { echo "FATAL: another backup/restore holds the lock" >&2; exit 1; }
[ ! -e "$DEST/db-$TS.sql.gz" ] || { echo "FATAL: snapshot timestamp collision" >&2; exit 1; }

# Compose invocation is overridable (DR drill); default = base stack + prod
# overlay when present. SC2086: $COMPOSE is intentionally word-split.
if [ -z "${COMPOSE:-}" ]; then
  COMPOSE="docker compose -f docker-compose.yml"
  [ -f docker-compose.prod.yml ] && COMPOSE="$COMPOSE -f docker-compose.prod.yml"
fi

dump_db() {
  # pg_dump runs in the db container (separate from backend); --clean --if-exists
  # makes the dump replayable over an existing DB.
  # shellcheck disable=SC2086
  $COMPOSE exec -T db sh -c 'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    | gzip > "$DEST/db-$TS.sql.gz"
}

echo "==> [$TS] consistent snapshot (Postgres + vectors + docs)"
if [ "$PAUSE_FOR_CONSISTENCY" = "true" ]; then
  # shellcheck disable=SC2086
  BACKEND_CID="$($COMPOSE ps -q backend || true)"
  [ -n "$BACKEND_CID" ] || { echo "FATAL: backend container not found - can't take a consistent snapshot" >&2; exit 1; }
  # Stop backend for the WHOLE dump+archive so the DB, vectors and docs are one
  # point in time. trap guarantees backend comes back even on failure.
  # Restart this exact container directly. `docker compose start backend` may
  # also re-run completed one-shot dependencies (for example model seeders),
  # making an otherwise short maintenance window unexpectedly long.
  trap 'docker start "$BACKEND_CID" >/dev/null 2>&1 || true' EXIT
  # shellcheck disable=SC2086
  $COMPOSE stop --timeout 120 backend
  STOP_CODE="$(docker inspect --format '{{.State.ExitCode}}' "$BACKEND_CID")"
  [[ "$STOP_CODE" = 0 || "$STOP_CODE" = 143 ]] || { echo "FATAL: backend did not stop cleanly; no snapshot taken" >&2; exit 1; }
  echo "    pg_dump (backend stopped)"
  dump_db
  echo "    tar vectors + docs (same stopped window)"
  # Stream the archive to the host instead of bind-mounting $DEST. Besides
  # being simpler, this works when a Linux shell (WSL) drives docker.exe: the
  # host path syntax then differs between the shell and Docker Desktop.
  docker run --rm --volumes-from "$BACKEND_CID" "$HELPER_IMAGE" \
    tar czf - -C / app/data app/docs > "$DEST/files-$TS.tar.gz"
  docker start "$BACKEND_CID" >/dev/null
  trap - EXIT
else
  echo "    PAUSE_FOR_CONSISTENCY=false - live dump+tar; relies on an external atomic snapshot"
  dump_db
  # shellcheck disable=SC2086
  $COMPOSE exec -T backend tar czf - -C / app/data app/docs > "$DEST/files-$TS.tar.gz"
fi

if [ -n "$BACKUP_SECRETS_RECIPIENT" ] && [ -f "$APP_DIR/.env" ]; then
  if command -v age >/dev/null 2>&1; then
    echo "==> [$TS] encrypting .env to the age recipient (private key stays off-VPS)"
    age -r "$BACKUP_SECRETS_RECIPIENT" -o "$DEST/env-$TS.age" "$APP_DIR/.env"
    chmod 600 "$DEST/env-$TS.age"
  else
    echo "WARNING: BACKUP_SECRETS_RECIPIENT set but 'age' is not installed - refusing to back up secrets in plaintext" >&2
  fi
fi

echo "==> [$TS] integrity manifest (sha256)"
( cd "$DEST" && sha256sum "db-$TS.sql.gz" "files-$TS.tar.gz" > "manifest-$TS.sha256.tmp"
  if [ -f "env-$TS.age" ]; then sha256sum "env-$TS.age" >> "manifest-$TS.sha256.tmp"; fi
  mv "manifest-$TS.sha256.tmp" "manifest-$TS.sha256" )
if ! python3 "$APP_DIR/scripts/backup_artifacts.py" verify "$DEST" "$TS"; then
  # Do not advertise an incomplete set as a restorable/rotatable snapshot.
  rm -f -- "$DEST/manifest-$TS.sha256"
  exit 1
fi

if [ -n "$OFFSITE_REMOTE" ]; then
  if command -v rclone >/dev/null 2>&1; then
    echo "==> Offsite copy -> $OFFSITE_REMOTE (use an rclone crypt remote)"
    rclone copy "$DEST/db-$TS.sql.gz"       "$OFFSITE_REMOTE"
    rclone copy "$DEST/files-$TS.tar.gz"    "$OFFSITE_REMOTE"
    if [ -f "$DEST/env-$TS.age" ]; then rclone copy "$DEST/env-$TS.age" "$OFFSITE_REMOTE"; fi
    # Publish manifest last, after every artifact upload succeeds.
    rclone copy "$DEST/manifest-$TS.sha256" "$OFFSITE_REMOTE"
  else
    echo "WARNING: OFFSITE_REMOTE set but rclone is not installed - skipping offsite copy" >&2
  fi
fi

echo "==> Pruning (keeping last $RETENTION snapshot sets)"
python3 "$APP_DIR/scripts/backup_artifacts.py" rotate "$DEST" "$RETENTION"

echo "==> Done:"
ls -lh "$DEST/db-$TS.sql.gz" "$DEST/files-$TS.tar.gz" "$DEST/manifest-$TS.sha256"
