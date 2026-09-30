#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
if [[ "${CAO_T020_DOCKER_ACCEPTANCE:-}" != "1" ]]; then
  echo "set CAO_T020_DOCKER_ACCEPTANCE=1 to run the local Docker acceptance suite" >&2
  exit 2
fi

docker_cli="${CAO_T020_DOCKER_CLI:-${CAO_T019_DOCKER_CLI:-docker}}"
if ! command -v "$docker_cli" >/dev/null 2>&1; then
  echo "Linux Docker CLI is unavailable; enable local Docker Engine integration" >&2
  exit 1
fi
docker_host="${CAO_T020_DOCKER_HOST:-${CAO_T019_DOCKER_HOST:-${DOCKER_HOST:-unix:///var/run/docker.sock}}}"
if [[ "$docker_host" != unix:///* ]]; then
  echo "T020 acceptance requires a local Docker Unix socket" >&2
  exit 1
fi
docker_socket="${docker_host#unix://}"
if [[ ! -S "$docker_socket" ]]; then
  echo "T020 acceptance cannot verify the local Docker socket" >&2
  exit 1
fi
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
engine_platform="$("$docker_cli" info --format '{{.OSType}}/{{.Architecture}}')" || {
  echo "T020 acceptance cannot reach the local Linux Docker Engine" >&2
  exit 1
}
case "$engine_platform" in
  linux/x86_64|linux/amd64) ;;
  *)
    echo "T020 acceptance requires a Linux amd64 Docker Engine (got $engine_platform)" >&2
    exit 1
    ;;
esac

printf 'Docker engine: '
"$docker_cli" version --format '{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}'
CAO_T019_DOCKER_CLI="$docker_cli" \
CAO_T019_DOCKER_HOST="$docker_host" \
  bash "$repo_root/test/integration/t019/build-work-rootfs.sh"
image_id="$("$docker_cli" image inspect --format '{{.Id}}' cao-work-empty-rootfs:local)"
if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "T019 returned an invalid immutable Docker image ID" >&2
  exit 1
fi

worker_build="$(mktemp -d)"
trap 'rm -rf "$worker_build"' EXIT
worker_binary="$worker_build/cao-agent-step-worker"
bash "$repo_root/test/integration/t020/build-worker.sh" "$worker_binary"
worker_sha256="$(sha256sum "$worker_binary" | cut -d ' ' -f 1)"

printf 'Pinned local Work rootfs image ID: %s\n' "$image_id"
export CAO_T020_ACCEPTANCE=1
export CAO_T020_DOCKER_IMAGE_ID="$image_id"
export CAO_T020_DOCKER_CLI="$docker_cli"
export CAO_T020_WORKER_BINARY="$worker_binary"
export CAO_T020_WORKER_SHA256="$worker_sha256"
export CAO_T019_DOCKER_IMAGE_ID="$image_id"
export CAO_T019_DOCKER_CLI="$docker_cli"
export CAO_WORK_DOCKER_LOCAL=1
export CAO_WORK_DOCKER_IMAGE_ID="$image_id"

uv run pytest -o addopts= -q -m integration \
  test/integration/t019/test_docker_backend.py \
  test/integration/t020/test_workflow_acceptance.py
