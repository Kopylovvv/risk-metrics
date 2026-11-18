#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 1 || $1 != /home/teahorses/risk-metrics-backups/* ]]; then
    echo "Usage: rollback.sh /home/teahorses/risk-metrics-backups/<release>" >&2
    exit 1
fi
backup_dir=$(realpath "$1")
[[ $backup_dir == /home/teahorses/risk-metrics-backups/* ]]
cd /home/teahorses/risk-metrics
exec 9>.git/deploy.lock
flock -w 900 9
target_sha=$(cat "$backup_dir/target-sha")
if [[ ! $target_sha =~ ^[0-9a-f]{40}$ || $(git rev-parse HEAD) != "$target_sha" ]]; then
    echo "Backup belongs to a different release; refusing a stale rollback." >&2
    exit 1
fi
current_schema=$(sudo -n docker compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"')
if [[ $current_schema != "$(cat "$backup_dir/schema-before")" ]]; then
    echo "Database schema changed. Review migration compatibility and restore plan first." >&2
    exit 1
fi
previous_sha=$(cat "$backup_dir/previous-sha")
[[ $previous_sha =~ ^[0-9a-f]{40}$ ]]
git diff --quiet
git diff --cached --quiet
git checkout --detach "$previous_sha"
files=(-f compose.yaml)
if [[ -f compose.override.yaml ]]; then files+=(-f compose.override.yaml); fi
sudo -n env APP_REVISION="$previous_sha" docker compose "${files[@]}" -f "$backup_dir/rollback-images.json" up -d --no-build --wait --wait-timeout 180
curl --fail --silent --show-error http://127.0.0.1:8080/health/ready
printf '%s\n' "$previous_sha" > .git/deployed-sha
printf '\nRolled back application to %s\n' "$previous_sha"
