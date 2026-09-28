#!/usr/bin/env bash
# Vedomo - restore from a backup made by scripts/backup.sh (Stage 54; hotfix:
# stop-first + clean-volume restore).
#
# DESTRUCTIVE: overwrites the live DB + ChromaDB vectors + uploaded docs with a
# backup snapshot. Run it in a maintenance window. Order (all with backend down,
# so nothing reads half-restored state and no stale files survive):
#   1. verify the sha256 manifest
#   2. stop backend
#   3. restore Postgres (--clean dump replays over the DB)
#   4. WIPE the current vectors+docs, then extract the snapshot (no orphans)
#   5. start backend
#
# Usage:
#   bash scripts/restore.sh <TIMESTAMP>            # e.g. 20260626-031500
#   bash scripts/restore.sh <TIMESTAMP> --dry-run  # verify artifacts only
#   FORCE=1 bash scripts/restore.sh <TIMESTAMP>    # skip the confirmation prompt
#   COMPOSE="docker compose -p bm_drill -f docker-compose.yml" \
#     FORCE=1 bash scripts/restore.sh <TS>         # isolated DR drill
#
# Secrets: if .env was lost too, restore it (password manager, or `age -d
# env-<TS>.age`) BEFORE running - the stack needs the same JWT/PG creds.
#
# List snapshots:  ls backups/db-*.sql.gz   Drill: design/backup-and-restore.md
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

DEST="${BACKUP_DIR:-$APP_DIR/backups}"
HELPER_IMAGE="${HELPER_IMAGE:-alpine:3}"
TS="${1:-}"
MODE="${2:-}"
if [[ ! "$TS" =~ ^[0-9]{8}-[0-9]{6}$ ]] || [[ -n "$MODE" && "$MODE" != "--dry-run" ]]; then
  echo "usage: restore.sh <timestamp> [--dry-run]   (see: ls $DEST/db-*.sql.gz)" >&2
  exit 2
fi

DB="$DEST/db-$TS.sql.gz"
FILES="$DEST/files-$TS.tar.gz"

for f in "$DB" "$FILES"; do
  [ -f "$f" ] || { echo "FATAL: missing backup artifact: $f" >&2; exit 1; }
done

command -v flock >/dev/null || { echo "FATAL: flock is required" >&2; exit 1; }
exec 9>"$APP_DIR/.backup.lock"
flock -n 9 || { echo "FATAL: a backup/restore is already running" >&2; exit 1; }
echo "==> Verifying required manifest, archives and safe extraction paths"
python3 "$APP_DIR/scripts/backup_artifacts.py" verify "$DEST" "$TS"

if [ "$MODE" = "--dry-run" ]; then
  echo "==> --dry-run: artifacts present and verified. Nothing restored."
  exit 0
fi

# Compose invocation is overridable (DR drill); default = base stack + prod
# overlay when present.
if [ -z "${COMPOSE:-}" ]; then
  COMPOSE="docker compose -f docker-compose.yml"
  [ -f docker-compose.prod.yml ] && COMPOSE="$COMPOSE -f docker-compose.prod.yml"
fi

if [ "${FORCE:-0}" != "1" ]; then
  echo
  echo "!!  This OVERWRITES the live database, vectors and uploaded files with"
  echo "!!  snapshot $TS. This cannot be undone."
  printf "    Type 'restore' to continue: "
  read -r reply
  [ "$reply" = "restore" ] || { echo "Aborted."; exit 1; }
fi

# shellcheck disable=SC2086
BACKEND_CID="$($COMPOSE ps -a -q backend)"
[ -n "$BACKEND_CID" ] || { echo "FATAL: backend container not found" >&2; exit 1; }
# Destructive target must be the exact named volumes on this backend.
MOUNTS="$(docker inspect --format '{{range .Mounts}}{{println .Type .Destination}}{{end}}' "$BACKEND_CID")"
for target in /app/data /app/docs; do
  grep -Fx "volume $target" <<< "$MOUNTS" >/dev/null || { echo "FATAL: expected named volume at $target" >&2; exit 1; }
done

echo "==> Stopping backend for the whole restore (no reads of half-restored state)"
trap 'echo "RESTORE INTERRUPTED: backend remains stopped; inspect and retry the complete restore before starting it." >&2' EXIT
# shellcheck disable=SC2086
$COMPOSE stop backend

echo "==> Restoring Postgres (--clean dump replays over the DB)"
# db stays up; backend (the app) is down.
# shellcheck disable=SC2086
gunzip -c "$DB" | $COMPOSE exec -T db sh -c 'psql --single-transaction -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

echo "==> Wiping current vectors+docs, then extracting the snapshot (no orphans)"
# Clear the volume CONTENTS (keep the mountpoints), then stream the verified
# archive into a helper that mounts the same volumes from the stopped backend.
# Streaming avoids host-path translation bugs when WSL drives Docker Desktop.
cat "$FILES" | docker run --rm -i --volumes-from "$BACKEND_CID" "$HELPER_IMAGE" \
  sh -c 'test -d /app/data && test ! -L /app/data && test -d /app/docs && test ! -L /app/docs && find /app/data /app/docs -mindepth 1 -delete && tar xzf - -C /'

# Start only the exact app container; Compose may otherwise re-run completed
# one-shot dependencies and prolong the maintenance window.
docker start "$BACKEND_CID" >/dev/null
trap - EXIT

echo "==> Restore of $TS complete. Verify: GET /api/health, log in, confirm a"
echo "    known material is present AND any post-snapshot material is gone, then"
echo "    run one search/answer over a restored material."
