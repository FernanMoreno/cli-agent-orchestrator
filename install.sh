#!/usr/bin/env sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
  echo "Se necesita Python 3 para el instalador; el servicio se ejecutará dentro de Docker." >&2
  exit 1
fi
exec python3 "$repo_dir/scripts/docker_install.py" "$@"
