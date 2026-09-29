#!/bin/sh
# Check a fresh pilot backup without touching the live database.
set -eu

backup=$1
case "$backup" in
  /opt/prodigy-max/backups/full-stack-*) ;;
  *) echo 'Unexpected backup path' >&2; exit 1 ;;
esac

cd "$backup"
sha256sum -c SHA256SUMS > /dev/null

container="nastavnik-backup-check-$$"
cleanup() {
  docker stop "$container" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run -d --rm --name "$container" --network none \
  --memory 256m --cpus 0.3 --pids-limit 80 \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine > /dev/null

ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec "$container" pg_isready -U postgres > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" = 1

docker exec -i "$container" pg_restore -U postgres -d postgres \
  --no-owner --no-privileges --exit-on-error < prodigy.dump

while IFS='|' read -r table expected; do
  case "$table" in
    ''|*[!A-Za-z0-9_]*) echo 'Unexpected table name' >&2; exit 1 ;;
  esac
  actual=$(docker exec "$container" psql -X -At -v ON_ERROR_STOP=1 \
    -U postgres -d postgres -c "SELECT count(*) FROM \"$table\"")
  test "$actual" = "$expected"
done < prodigy.counts

echo 'Backup checksums and isolated Prodigy table counts passed.'
