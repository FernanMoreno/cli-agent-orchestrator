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

image_tag="cao-t019-qemu-acceptance:local"
"$docker_cli" build --pull --platform linux/amd64 \
  --file "$dockerfile" \
  --tag "$image_tag" \
  "$docker_context"
image_id="$("$docker_cli" image inspect --format '{{.Id}}' "$image_tag")"
printf 'Acceptance runner image ID: %s\n' "$image_id"

"$docker_cli" run --rm --init \
  --platform linux/amd64 \
  --memory 8g \
  --cpus 4 \
  --mount "type=bind,source=$docker_workspace,target=/workspace,readonly" \
  --env GITHUB_WORKSPACE=/workspace \
  --env RUNNER_TEMP=/tmp \
  --env GITHUB_REF=refs/heads/main \
  --env CAO_T019_LOCAL_DOCKER_ACCEPTANCE=1 \
  --env CAO_WORK_BROKER_ACCOUNT=cao_t019_broker \
  "$image_id" \
  bash /workspace/.github/scripts/t097-qemu-acceptance.sh
