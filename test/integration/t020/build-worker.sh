#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

worker_source="$repo_root/test/integration/t020/agent-step-worker.c"
worker_pin="$repo_root/test/integration/t020/agent-step-worker.sha256"
worker_output="${1:-}"

if ! command -v cc >/dev/null 2>&1 || ! command -v readelf >/dev/null 2>&1; then
  echo "a static C compiler and readelf are required to build the T020 worker" >&2
  exit 1
fi
if [[ -z "$worker_output" ]]; then
  echo "usage: $0 OUTPUT_PATH" >&2
  exit 2
fi
if ! sha256sum --check --status "$worker_pin"; then
  echo "T020 deterministic worker source differs from its pinned digest" >&2
  exit 1
fi

mkdir -p "$(dirname "$worker_output")"
cc -static -O2 -Wall -Wextra -Werror "$worker_source" -o "$worker_output"
if readelf -l "$worker_output" | rg -q 'INTERP'; then
  echo "T020 worker unexpectedly requires a dynamic loader" >&2
  exit 1
fi

printf 'Worker source SHA-256: '
sha256sum "$worker_source"
printf 'Worker ELF SHA-256: '
sha256sum "$worker_output"
