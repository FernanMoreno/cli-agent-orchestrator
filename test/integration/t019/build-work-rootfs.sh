#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source_file="$repo_root/src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
docker_cli="${CAO_T019_DOCKER_CLI:-docker}"

if ! command -v "$docker_cli" >/dev/null 2>&1; then
  echo "Linux Docker CLI is unavailable" >&2
  exit 1
fi
docker_host="${CAO_T019_DOCKER_HOST:-${DOCKER_HOST:-unix:///var/run/docker.sock}}"
if [[ "$docker_host" != unix:///* ]]; then
  echo "T019 rootfs build requires a local Docker Unix socket" >&2
  exit 1
fi
docker_socket="${docker_host#unix://}"
if [[ ! -S "$docker_socket" ]]; then
  echo "T019 rootfs build cannot verify the local Docker socket" >&2
  exit 1
fi
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
if ! "$docker_cli" info --format '{{.OSType}}/{{.Architecture}}' >/dev/null; then
  echo "T019 rootfs build cannot reach the local Docker Engine" >&2
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
