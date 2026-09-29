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

CAO_T019_DOCKER_CLI="$docker_cli" \
  bash "$repo_root/test/integration/t019/build-work-rootfs.sh"
image_id="$("$docker_cli" image inspect --format '{{.Id}}' cao-work-empty-rootfs:local)"
printf 'Docker Work rootfs image ID: %s\n' "$image_id"

CAO_T019_DOCKER_CLI="$docker_cli" \
CAO_T019_DOCKER_IMAGE_ID="$image_id" \
  uv run pytest -o addopts= -q -m integration \
    test/integration/t019/test_docker_backend.py
