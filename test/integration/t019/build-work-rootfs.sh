#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source_file="$repo_root/src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
docker_cli="${CAO_T019_DOCKER_CLI:-}"
if [[ -z "$docker_cli" ]]; then
  docker_cli=docker
  if command -v docker.exe >/dev/null 2>&1; then
    docker_cli=docker.exe
  fi
fi

if ! command -v "$docker_cli" >/dev/null 2>&1; then
  echo "Docker CLI is unavailable" >&2
  exit 1
fi
if ! command -v cc >/dev/null 2>&1; then
  echo "a C compiler is required to build the static Docker supervisor" >&2
  exit 1
fi

build_context="$(mktemp -d)"
trap 'rm -rf "$build_context"' EXIT
supervisor_source_sha256="$(sha256sum "$source_file" | cut -d ' ' -f 1)"
cc -static -O2 -s -Wall -Wextra -Werror \
  -D"CAO_SUPERVISOR_SOURCE_SHA256=\"$supervisor_source_sha256\"" \
  "$source_file" -o "$build_context/cao-work-supervisor"
if readelf -l "$build_context/cao-work-supervisor" | rg -q 'INTERP'; then
  echo "Docker supervisor unexpectedly has a dynamic loader" >&2
  exit 1
fi
cp "$repo_root/test/integration/t019/work-rootfs.Dockerfile" "$build_context/Dockerfile"

printf 'Supervisor source SHA-256: %s\n' "$supervisor_source_sha256"
printf 'Supervisor binary: '
sha256sum "$build_context/cao-work-supervisor"

image_tag="cao-work-empty-rootfs:local"
tar -C "$build_context" -cf - Dockerfile cao-work-supervisor | \
  "$docker_cli" build --platform linux/amd64 --network=none \
  --build-arg "CAO_WORK_SUPERVISOR_SOURCE_SHA256=$supervisor_source_sha256" \
  --tag "$image_tag" \
  -
image_id="$("$docker_cli" image inspect --format '{{.Id}}' "$image_tag")"
printf 'CAO_WORK_DOCKER_IMAGE_ID=%s\n' "$image_id"
