#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
docker_cli="${CAO_T019_DOCKER_CLI:-docker}"
if ! command -v "$docker_cli" >/dev/null 2>&1; then
  echo "Linux Docker CLI is unavailable; install Docker Engine or Docker Desktop WSL integration" >&2
  exit 1
fi

docker_host="${CAO_T019_DOCKER_HOST:-${DOCKER_HOST:-unix:///var/run/docker.sock}}"
if [[ "$docker_host" != unix:///* ]]; then
  echo "T019 acceptance requires a local Docker Unix socket" >&2
  exit 1
fi
docker_socket="${docker_host#unix://}"
if [[ ! -S "$docker_socket" ]]; then
  echo "T019 acceptance cannot verify the local Docker socket" >&2
  exit 1
fi
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
if ! "$docker_cli" info --format '{{.OSType}}/{{.Architecture}}' >/dev/null; then
  echo "T019 acceptance cannot reach the local Docker Engine" >&2
  exit 1
fi

CAO_T019_DOCKER_CLI="$docker_cli" \
  bash "$repo_root/test/integration/t019/build-work-rootfs.sh"
image_id="$("$docker_cli" image inspect --format '{{.Id}}' cao-work-empty-rootfs:local)"
printf 'Docker Work rootfs image ID: %s\n' "$image_id"

CAO_T019_DOCKER_CLI="$docker_cli" \
CAO_T019_DOCKER_IMAGE_ID="$image_id" \
  uv run pytest -o addopts= -q -m integration \
    test/integration/t019/test_docker_backend.py
