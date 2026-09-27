#!/bin/sh
# Run on the MAX pilot host. Restoring this backup must use separate resources.
set -eu
umask 077
cd /opt/prodigy-max

prodigy() {
    docker compose --project-name prodigy-max-pilot --env-file .env \
        -f compose.yml -f compose.chat-ux.yml -f compose.knowledge-ui.yml \
        -f compose.chat-navigation.yml -f compose.onboarding-guide.yml \
        -f compose.ui-integration.yml -f compose.tester-ready.yml \
        -f compose.document-import.yml "$@"
}

vedomo() {
    docker compose --project-directory /opt/vedomo-max --project-name vedomo-max-pilot \
        --env-file /opt/vedomo-max/.env --env-file /opt/vedomo-max/.env.gigachat \
        -f /opt/vedomo-max/docker-compose.yml \
        -f /opt/vedomo-max/docker-compose.max-shared.yml \
        -f /opt/vedomo-max/docker-compose.max-gigachat.yml \
        -f /opt/vedomo-max/docker-compose.max-onboarding.yml \
        -f /opt/vedomo-max/docker-compose.max-knowledge.yml \
        -f /opt/vedomo-max/docker-compose.max-document-import.yml "$@"
}

sql() {
    docker exec "$1" sh -c 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "$2"
}

counts() {
    container=$1
    sql "$container" "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename" |
        while IFS= read -r table; do
            case "$table" in *[!A-Za-z0-9_]*) exit 1 ;; esac
            count=$(sql "$container" "SELECT count(*) FROM \"$table\"")
            printf '%s|%s\n' "$table" "$count"
        done
}

archive_volume() {
    volume=$1
    destination=$2
    mount=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
    [ "$mount" = "/var/lib/docker/volumes/$volume/_data" ]
    tar -C "$mount" -czf "$destination" .
}

prodigy config --quiet
vedomo config --quiet
[ "$(prodigy ps -q web)" = "$(docker inspect --format '{{.Id}}' prodigy-max-pilot-web-1)" ]
[ "$(vedomo ps -q backend)" = "$(docker inspect --format '{{.Id}}' vedomo-max-pilot-backend-1)" ]
[ "$(sql prodigy-max-pilot-db-1 "SELECT count(*) FROM \"MaxBotDelivery\" WHERE status='SENDING'")" = 0 ]
[ "$(sql vedomo-max-pilot-db-1 "SELECT count(*) FROM documents WHERE status='processing'")" = 0 ]

backup=$(mktemp -d /opt/prodigy-max/backups/full-stack-XXXXXXXX)
paused=false
resume() {
    if [ "$paused" = true ]; then
        vedomo start backend
        prodigy start web worker
        paused=false
    fi
}
trap resume EXIT
trap 'exit 130' INT TERM
paused=true
prodigy stop --timeout 30 web worker
vedomo stop --timeout 30 backend
[ "$(sql prodigy-max-pilot-db-1 "SELECT count(*) FROM \"MaxBotDelivery\" WHERE status='SENDING'")" = 0 ]
[ "$(sql vedomo-max-pilot-db-1 "SELECT count(*) FROM documents WHERE status='processing'")" = 0 ]

for pair in prodigy:prodigy-max-pilot-db-1 vedomo:vedomo-max-pilot-db-1; do
    name=${pair%%:*}
    container=${pair#*:}
    docker exec "$container" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' > "$backup/$name.dump"
    counts "$container" > "$backup/$name.counts"
    [ -s "$backup/$name.counts" ]
    docker exec -i "$container" pg_restore --list < "$backup/$name.dump" > /dev/null
done
archive_volume prodigy-max-pilot_documents "$backup/prodigy-documents.tar.gz"
archive_volume vedomo-max-pilot_docs "$backup/vedomo-documents.tar.gz"
archive_volume vedomo-max-pilot_data "$backup/vedomo-data.tar.gz"

mkdir "$backup/config"
cp .env compose*.yml nginx.conf "$backup/config/"
mkdir "$backup/config/vedomo"
cp /opt/vedomo-max/.env /opt/vedomo-max/.env.gigachat \
    /opt/vedomo-max/docker-compose*.yml /opt/vedomo-max/gigachat-ca.pem "$backup/config/vedomo/"
cp /opt/vedomo-max/docker/nginx.max-shared.conf "$backup/config/vedomo/"
for container in prodigy-max-pilot-web-1 prodigy-max-pilot-worker-1 vedomo-max-pilot-backend-1; do
    docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' "$container"
done > "$backup/images.txt"
printf '%s\n' 'BGE-M3 snapshot: 5617a9f61b028005a4858fdac845db406aefb181' > "$backup/model.txt"
(
    cd "$backup"
    find . -type f ! -name SHA256SUMS -exec sha256sum '{}' \; | sort > SHA256SUMS
    sha256sum -c SHA256SUMS > /dev/null
)
resume
printf 'FULL_BACKUP=%s\n' "$backup"
printf '%s\n' 'Both applications resumed. Private configuration stays on the server.'
