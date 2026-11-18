#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# != 1 || ! $1 =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: deploy.sh <full-commit-sha>" >&2
    exit 1
fi
target_sha=$1
cd /home/teahorses/risk-metrics
exec 9>.git/deploy.lock
flock -w 900 9

git fetch --prune origin
# Only commits reachable from the production branch are eligible.
git cat-file -e "${target_sha}^{commit}"
git merge-base --is-ancestor "$target_sha" origin/main
previous_sha=$(git rev-parse HEAD)
git diff --quiet
git diff --cached --quiet
if [[ $previous_sha != "$target_sha" ]]; then
    git merge-base --is-ancestor "$previous_sha" "$target_sha" || {
        echo "A newer or divergent release is installed; refusing a stale deployment" >&2
        exit 1
    }
fi

mkdir -p /home/teahorses/risk-metrics-backups
backup_dir=$(mktemp -d "/home/teahorses/risk-metrics-backups/$(date -u +%Y%m%dT%H%M%SZ)-${target_sha:0:12}.XXXXXX")
printf '%s\n' "$previous_sha" > "$backup_dir/previous-sha"
printf '%s\n' "$target_sha" > "$backup_dir/target-sha"
cp .env "$backup_dir/environment"
if [[ -f compose.override.yaml ]]; then
    cp compose.override.yaml "$backup_dir/compose.override.yaml"
fi
compose() {
    local files=(-f compose.yaml)
    if [[ -f compose.override.yaml ]]; then files+=(-f compose.override.yaml); fi
    local envs=(--env-file .env)
    if [[ -f compose.monitoring.yaml && -f .monitoring.env ]]; then
        files+=(-f compose.monitoring.yaml)
        envs+=(--env-file .monitoring.env)
    fi
    sudo -n env APP_REVISION="$target_sha" COMPOSE_PARALLEL_LIMIT=1 docker compose "${envs[@]}" "${files[@]}" "$@"
}

# Retain the exact previous images for a code rollback without rebuilding dependencies.
sudo -n docker inspect risk-metrics-backend-1 risk-metrics-frontend-1 > "$backup_dir/containers.json"
python3 - "$backup_dir" <<'PY_IMAGES'
import json,sys,subprocess
from pathlib import Path
p=Path(sys.argv[1]); containers=json.loads((p/'containers.json').read_text())
services={}
for c in containers:
    name=c['Config']['Labels']['com.docker.compose.service']
    # Pin the image reference before rebuilding its mutable Compose tag.
    # Docker's container .Image may be a config digest rather than an inspectable image ID.
    pin=f'risk-metrics-rollback/{name}:{p.name}'
    subprocess.run(['sudo','-n','docker','tag',c['Config']['Image'],pin],check=True)
    services[name]={'image':pin,'pull_policy':'never'}
    if name=='backend': services['migrate']={'image':pin,'pull_policy':'never'}
(p/'rollback-images.json').write_text(json.dumps({'services':services}))
PY_IMAGES
compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
compose exec -T db pg_restore --list < "$backup_dir/database.dump" > "$backup_dir/dump-contents.txt"
compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"' > "$backup_dir/schema-before"
# Restore into an isolated database on the same PostgreSQL instance before proceeding.
restore_db=restore_check_$(date +%s)_$$
compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; createdb -h 127.0.0.1 -U "$POSTGRES_USER" "$1"' sh "$restore_db"
cleanup_restore() {
    compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; dropdb --if-exists -h 127.0.0.1 -U "$POSTGRES_USER" "$1"' sh "$restore_db" || true
}
trap cleanup_restore EXIT
compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore --exit-on-error --no-owner -h 127.0.0.1 -U "$POSTGRES_USER" -d "$1"' sh "$restore_db" < "$backup_dir/database.dump"
cleanup_restore
trap - EXIT

# Build while the old containers still serve traffic. The DB volume and .env stay on the VM.
# Repository content must be readable by non-root image users and bind-mounted services.
# Private backups and server environment files keep restrictive permissions.
(umask 022; git checkout --detach "$target_sha")
python3 scripts/prepare_checkout.py
if ! compose build backend migrate frontend; then
    git checkout --detach "$previous_sha"
    exit 1
fi
if ! compose up -d --no-build --wait --wait-timeout 180; then
    echo "Deployment failed. Backup: $backup_dir" >&2
    compose logs --no-color --tail=40 migrate backend >&2 || true
    exit 1
fi
curl --fail --silent --show-error --retry 12 --retry-connrefused --retry-delay 2 http://127.0.0.1:8080/health/ready
for service in backend frontend; do
    container=$(compose ps -q "$service")
    installed=$(sudo -n docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container")
    [[ $installed == "$target_sha" ]]
done
cmp .env "$backup_dir/environment"
printf '%s\n' "$target_sha" > .git/deployed-sha
printf '%s\n' "$backup_dir" > .git/last-deploy-backup
printf '\nDeployed %s; previous %s; backup %s\n' "$target_sha" "$previous_sha" "$backup_dir"
