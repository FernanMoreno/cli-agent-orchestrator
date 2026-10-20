#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
docker_cli="${CAO_T019_DOCKER_CLI:-}"
if [[ -z "$docker_cli" ]]; then
  docker_cli=docker
  if command -v docker.exe >/dev/null 2>&1; then
    docker_cli=docker.exe
  fi
fi
if ! command -v "$docker_cli" >/dev/null 2>&1; then
  echo "Docker CLI is unavailable; install Docker Desktop or Docker Engine" >&2
  exit 1
fi
if [[ "$docker_cli" == *.exe ]]; then
  echo "T097 PostgreSQL guest acceptance requires the Linux Docker CLI" >&2
  exit 1
fi

available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
if [[ ! "$available_kib" =~ ^[0-9]+$ || "$available_kib" -lt 2097152 ]]; then
  available_mib="unknown"
  if [[ "$available_kib" =~ ^[0-9]+$ ]]; then
    available_mib="$((available_kib / 1024))"
  fi
  echo "T097 local QEMU acceptance needs at least 2048 MiB available; observed ${available_mib} MiB" >&2
  exit 1
fi

docker_context="$repo_root/test/integration/t019"
dockerfile="$docker_context/acceptance.Dockerfile"
docker_workspace="$repo_root"
if [[ "$docker_cli" == *.exe ]]; then
  docker_context="$(wslpath -w "$docker_context")"
  dockerfile="$(wslpath -w "$dockerfile")"
  docker_workspace="$(wslpath -w "$repo_root")"
fi

printf 'Docker CLI: '
"$docker_cli" version --format '{{.Client.Version}}'
printf 'Docker engine: '
"$docker_cli" info --format '{{.OSType}} {{.Architecture}} {{.KernelVersion}} {{json .SecurityOptions}}'

mapfile -t postgres_candidates < <(
  "$docker_cli" ps --no-trunc --filter label=org.cao.owner=spec021-runtime --quiet
)
if [[ "${#postgres_candidates[@]}" -ne 1 ]]; then
  echo "T097 requires exactly one running Spec 021 PostgreSQL test service" >&2
  exit 1
fi
postgres_container_id="${postgres_candidates[0]}"
postgres_image_id="$("$docker_cli" inspect --format '{{.Image}}' "$postgres_container_id")"
postgres_image_ref="$("$docker_cli" inspect --format '{{.Config.Image}}' "$postgres_container_id")"
postgres_run_id="$("$docker_cli" inspect --format '{{index .Config.Labels "org.cao.run"}}' "$postgres_container_id")"
postgres_owner="$("$docker_cli" inspect --format '{{index .Config.Labels "org.cao.owner"}}' "$postgres_container_id")"
postgres_name="$("$docker_cli" inspect --format '{{.Name}}' "$postgres_container_id")"
postgres_name="${postgres_name#/}"
mapfile -t postgres_networks < <(
  "$docker_cli" inspect --format \
    '{{range $name, $network := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
    "$postgres_container_id" | awk 'NF'
)
postgres_host_ip="$("$docker_cli" inspect --format \
  '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostIp}}' "$postgres_container_id")"
postgres_host_port="$("$docker_cli" inspect --format \
  '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$postgres_container_id")"
postgres_secret_source="$("$docker_cli" inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/run/secrets/postgres_password"}}{{.Source}}{{end}}{{end}}' \
  "$postgres_container_id")"
postgres_secret_mode="$(stat -c '%a' "$postgres_secret_source" 2>/dev/null || true)"
postgres_secret_owner="$(stat -c '%u' "$postgres_secret_source" 2>/dev/null || true)"
postgres_state="$("$docker_cli" inspect --format '{{.State.Health.Status}}' "$postgres_container_id")"
postgres_tmpfs="$("$docker_cli" inspect --format '{{index .HostConfig.Tmpfs "/var/lib/postgresql"}}' \
  "$postgres_container_id")"
if [[ "$postgres_owner" != spec021-runtime \
  || ! "$postgres_run_id" =~ ^[a-f0-9]{12}$ \
  || "$postgres_name" != "cao-spec021-pg-${postgres_run_id}" \
  || "$postgres_image_id" != sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336 \
  || "$postgres_image_ref" != postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336 \
  || "${#postgres_networks[@]}" -ne 1 \
  || "${postgres_networks[0]}" != "cao-spec021-test-net-${postgres_run_id}" \
  || "$postgres_host_ip" != 127.0.0.1 \
  || ! "$postgres_host_port" =~ ^[0-9]+$ \
  || "$postgres_secret_mode" != 600 \
  || "$postgres_secret_owner" != "$(id -u)" \
  || "$postgres_state" != healthy \
  || -z "$postgres_tmpfs" ]]; then
  echo "The running PostgreSQL service does not match the owned pinned test contract" >&2
  exit 1
fi
network_driver="$("$docker_cli" network inspect --format '{{.Driver}}' "${postgres_networks[0]}")"
network_internal="$("$docker_cli" network inspect --format '{{.Internal}}' "${postgres_networks[0]}")"
if [[ "$network_driver" != bridge || "$network_internal" != false ]]; then
  echo "T097 requires the owned PostgreSQL bridge network to retain runner egress" >&2
  exit 1
fi

postgres_marker_nonce="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
postgres_marker_database="t097_marker_${postgres_marker_nonce}"
postgres_marker_created=false
cleanup_postgres_marker() {
  local status=$?
  trap - EXIT
  if [[ "$postgres_marker_created" == true ]]; then
    "$docker_cli" exec "$postgres_container_id" sh -c \
      'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec psql -X -q -v ON_ERROR_STOP=1 -U cao_spec021_admin -d postgres -c "DROP DATABASE IF EXISTS '"$postgres_marker_database"'"' \
      >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup_postgres_marker EXIT
"$docker_cli" exec "$postgres_container_id" sh -c \
  'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec psql -X -q -v ON_ERROR_STOP=1 -U cao_spec021_admin -d postgres -c "CREATE DATABASE '"$postgres_marker_database"' OWNER cao_spec021_admin"' \
  >/dev/null
postgres_marker_created=true
"$docker_cli" exec -i "$postgres_container_id" sh -c \
  'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec psql -X -q -v ON_ERROR_STOP=1 -U cao_spec021_admin -d '"$postgres_marker_database"'' \
  >/dev/null <<SQL
CREATE TABLE public.t097_service_identity (
  nonce text NOT NULL,
  container_id text NOT NULL,
  run_id text NOT NULL,
  image_id text NOT NULL
);
INSERT INTO public.t097_service_identity VALUES (
  '$postgres_marker_nonce', '$postgres_container_id', '$postgres_run_id',
  'sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336'
);
REVOKE ALL ON public.t097_service_identity FROM PUBLIC;
SQL

image_tag="cao-t019-qemu-acceptance:local"
"$docker_cli" build --pull --platform linux/amd64 \
  --file "$dockerfile" \
  --tag "$image_tag" \
  "$docker_context"
image_id="$("$docker_cli" image inspect --format '{{.Id}}' "$image_tag")"
printf 'Acceptance runner image ID: %s\n' "$image_id"

"$docker_cli" run --rm --init \
  --platform linux/amd64 \
  --network "${postgres_networks[0]}" \
  --memory 3g \
  --cpus 2 \
  --mount "type=bind,source=$docker_workspace,target=/workspace,readonly" \
  --mount "type=bind,source=$postgres_secret_source,target=/run/secrets/cao-t097-postgres-password,readonly" \
  --env GITHUB_WORKSPACE=/workspace \
  --env RUNNER_TEMP=/tmp \
  --env CAO_T019_LOCAL_DOCKER_ACCEPTANCE=1 \
  --env CAO_T097_PG_CONTAINER_ID="$postgres_container_id" \
  --env CAO_T097_PG_RUN_ID="$postgres_run_id" \
  --env CAO_T097_PG_IMAGE_ID="$postgres_image_id" \
  --env CAO_T097_PG_NETWORK_NAME="${postgres_networks[0]}" \
  --env CAO_T097_PG_UPSTREAM_HOST="$postgres_name" \
  --env CAO_T097_PG_UPSTREAM_PORT=5432 \
  --env CAO_T097_PG_MARKER_DB="$postgres_marker_database" \
  --env CAO_T097_PG_MARKER_NONCE="$postgres_marker_nonce" \
  --env CAO_T097_PG_PASSWORD_FILE=/run/secrets/cao-t097-postgres-password \
  --env CAO_WORK_BROKER_ACCOUNT=cao_t019_broker \
  "$image_id" \
  bash /workspace/.github/scripts/t097-qemu-acceptance.sh
