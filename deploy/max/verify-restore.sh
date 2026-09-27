#!/bin/sh
# Restore into new private resources only. No live database reset or volume removal.
set -eu
umask 077
cd /opt/prodigy-max
test "$(pwd -P)" = /opt/prodigy-max
test "$(docker inspect --format '{{.Config.Image}}' prodigy-max-pilot-web-1)" = prodigy-max:tester-ready-v2-20260927
test "$(docker inspect --format '{{.Config.Image}}' prodigy-max-pilot-db-1)" = postgres:16-alpine
docker volume inspect prodigy-max-pilot_documents >/dev/null
test -s /opt/prodigy-max/max-restore-fixture-20260927.ts
# Abort before creating a fixture or pausing the pilot if the shared disk is busy.
disk_wait=$(vmstat 1 2 | awk 'END {print $16}')
test "$disk_wait" -lt 25

review=prodigy-max-restore-$(cat /proc/sys/kernel/random/uuid)
verify_only=0
if test "${1:-}" = --verify-backup; then
  test "$#" = 2
  backup=$(readlink -f "$2")
  verify_only=1
else
  test "$#" = 0
  backup=$(mktemp -d /opt/prodigy-max/backups/restore-pair-XXXXXX)
fi
case "$backup" in /opt/prodigy-max/backups/restore-pair-*) ;; *) exit 1 ;; esac
if test "$verify_only" = 1; then
  chmod 600 "$backup/database.dump" "$backup/documents.tar.gz"
  sha256sum -c "$backup/SHA256SUMS"
  probe=$(cat "$backup/probe.txt")
else
  probe=max-restore-probe-$(cat /proc/sys/kernel/random/uuid)
fi
stopped=0
fixture=0
review_started=0
counts='SELECT (SELECT count(*) FROM "User"), (SELECT count(*) FROM "Course"), (SELECT count(*) FROM "QuizAttempt"), (SELECT count(*) FROM "Certificate"), (SELECT count(*) FROM "MaxCourseDocument"), (SELECT count(*) FROM "MaxBotDelivery"), (SELECT count(*) FROM "_prisma_migrations");'

prodigy() {
  docker compose -f compose.yml -f compose.chat-ux.yml -f compose.knowledge-ui.yml -f compose.chat-navigation.yml -f compose.onboarding-guide.yml -f compose.ui-integration.yml -f compose.tester-ready.yml "$@"
}
fixture_command() {
  prodigy run --rm -T --no-deps --entrypoint node \
    -v /opt/prodigy-max/max-restore-fixture-20260927.ts:/app/scripts/max-restore-fixture.ts:ro \
    web node_modules/tsx/dist/cli.mjs scripts/max-restore-fixture.ts "$1" "$probe"
}
resume() {
  prodigy up -d --no-deps --no-build --wait --wait-timeout 120 web || return 1
  prodigy up -d --no-deps --no-build worker || return 1
  stopped=0
}
finish() {
  status=$?
  trap - EXIT
  if test "$stopped" = 1; then
    if ! resume; then
      echo 'LIVE_PILOT_NEEDS_ATTENTION: resume failed.' >&2
      status=1
    fi
  fi
  if test "$fixture" = 1; then
    fixture_command clean || status=1
  fi
  if test "$review_started" = 1; then
    docker stop "$review-db" >/dev/null || status=1
  fi
  echo "Backup directory kept at $backup. No review volumes deleted."
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if test "$verify_only" = 0; then
  fixture_command create
  fixture=1
  # Quiesce only this pilot so the dump and file archive describe the same moment.
  stopped=1
  prodigy stop --timeout 55 worker web
  inflight=$(docker exec prodigy-max-pilot-db-1 psql -U prodigy_max -d prodigy_max -Atc \
    'SELECT count(*) FROM "MaxBotDelivery" WHERE status = '\''SENDING'\'';')
  test "$inflight" = 0
  docker exec prodigy-max-pilot-db-1 psql -U prodigy_max -d prodigy_max -Atc "$counts" > "$backup/counts.txt"
  docker exec prodigy-max-pilot-db-1 pg_dump -U prodigy_max -d prodigy_max -Fc > "$backup/database.dump"
  docker run --rm --network none --memory 96m --cpus 0.2 --pids-limit 32 \
    --mount source=prodigy-max-pilot_documents,target=/source,readonly \
    --mount "type=bind,source=$backup,target=/backup" \
    --entrypoint tar nginx:stable-alpine -czf /backup/documents.tar.gz -C /source .
  chmod 600 "$backup/database.dump" "$backup/documents.tar.gz"
  cp .env "$backup/pilot.env"
  cp nginx.conf "$backup/nginx.conf"
  printf '%s\n' "$probe" > "$backup/probe.txt"
  docker exec -i prodigy-max-pilot-db-1 pg_restore -l < "$backup/database.dump" >/dev/null
  sha256sum "$backup/database.dump" "$backup/documents.tar.gz" > "$backup/SHA256SUMS"
  resume
fi

docker network create --internal --label "max.restore=$review" "$review" >/dev/null
docker volume create --label "max.restore=$review" "$review-db" >/dev/null
docker volume create --label "max.restore=$review" "$review-documents" >/dev/null
# Trust applies only to a new unpublished database on this isolated network.
# No bot, GigaChat or production database credentials are copied into review containers.
docker run -d --name "$review-db" --label "max.restore=$review" \
  --network "$review" --network-alias restore-db --memory 256m --cpus 0.3 --pids-limit 80 \
  --mount "source=$review-db,target=/var/lib/postgresql/data" \
  -e POSTGRES_USER=max_restore_review -e POSTGRES_DB=max_restore_review \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine >/dev/null
review_started=1
ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec "$review-db" psql -U max_restore_review -d max_restore_review -Atc 'SELECT 1' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" = 1
docker exec -i "$review-db" pg_restore -U max_restore_review -d max_restore_review \
  --no-owner --no-privileges --exit-on-error < "$backup/database.dump"
docker exec "$review-db" psql -U max_restore_review -d max_restore_review -Atc "$counts" > "$backup/restored-counts.txt"
cmp "$backup/counts.txt" "$backup/restored-counts.txt"
docker run --rm --network none --memory 96m --cpus 0.2 --pids-limit 32 \
  --mount "source=$review-documents,target=/restore" \
  --mount "type=bind,source=$backup,target=/backup,readonly" \
  --entrypoint tar nginx:stable-alpine -xzf /backup/documents.tar.gz -C /restore
docker run --rm --network "$review" --memory 256m --cpus 0.3 --pids-limit 64 \
  --mount "source=$review-documents,target=/app/data/uploads,readonly" \
  --mount type=bind,source=/opt/prodigy-max/max-restore-fixture-20260927.ts,target=/app/scripts/max-restore-fixture.ts,readonly \
  -e DATABASE_URL=postgresql://max_restore_review@restore-db:5432/max_restore_review \
  -e MAX_DISABLE_EMAIL=true -e MAX_RESTORE_REVIEW=true \
  --entrypoint node prodigy-max:tester-ready-v2-20260927 \
  node_modules/tsx/dist/cli.mjs scripts/max-restore-fixture.ts verify "$probe"
echo 'RESTORE_PAIR_PASSED: row counts, approved source and private original bytes match.'
