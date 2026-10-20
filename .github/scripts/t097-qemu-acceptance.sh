#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?GitHub workspace is required}"
: "${RUNNER_TEMP:?GitHub runner temp directory is required}"
: "${CAO_WORK_BROKER_ACCOUNT:?Set the repository variable CAO_WORK_BROKER_ACCOUNT}"

local_docker_acceptance="${CAO_T019_LOCAL_DOCKER_ACCEPTANCE:-0}"
host_prereq_cache="${CAO_T097_HOST_PREREQ_CACHE:-0}"
if [[ "$host_prereq_cache" != 0 && "$host_prereq_cache" != 1 ]]; then
  echo "CAO_T097_HOST_PREREQ_CACHE must be 0 or 1" >&2
  exit 1
fi
if [[ "$host_prereq_cache" == 0 ]] \
  && { [[ -n "${CAO_T097_GUEST_IMAGE_CACHE_PATH+x}" ]] || [[ -n "${CAO_T097_GUEST_IMAGE_CACHE_SHA256+x}" ]]; }; then
  echo "guest image cache bindings require CAO_T097_HOST_PREREQ_CACHE=1" >&2
  exit 1
fi
if [[ "$local_docker_acceptance" != 0 && "$local_docker_acceptance" != 1 ]]; then
  echo "CAO_T019_LOCAL_DOCKER_ACCEPTANCE must be 0 or 1" >&2
  exit 1
fi
if [[ "$local_docker_acceptance" != 1 && "${GITHUB_REF:-}" != refs/heads/main ]]; then
  echo "T097 guest acceptance may run only from main" >&2
  exit 1
fi
if [[ ! "$CAO_WORK_BROKER_ACCOUNT" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
  echo "CAO_WORK_BROKER_ACCOUNT is not a valid local account name" >&2
  exit 1
fi

readonly guest_image_sha256=6001247f681e87448e3f403b2e5ca859fb23a6cbbf0813f01a456a072b70a529
readonly guest_image_url=https://cloud-images.ubuntu.com/stonking/20260919/stonking-server-cloudimg-amd64.img
readonly guest_image_container_path=/opt/t097-prereq-cache/stonking-server-cloudimg-amd64.img
readonly bubblewrap_source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
readonly guest_port=22223
readonly guest=runner@127.0.0.1
if [[ "$local_docker_acceptance" == 1 ]]; then
  readonly qemu_memory_mb=1536
  readonly qemu_vcpu_count=2
else
  readonly qemu_memory_mb=4096
  readonly qemu_vcpu_count=4
fi

work_dir="$(mktemp -d "${RUNNER_TEMP}/t097-qemu.XXXXXX")"
qemu_pid=""
postgres_proxy_pid=""
postgres_fixture_cli=""
postgres_fixture_container_id=""
postgres_fixture_container_name=""
postgres_fixture_network=""
postgres_fixture_network_id=""
postgres_marker_database=""
postgres_marker_created=false
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$status" -ne 0 ]]; then
    if [[ -f "$work_dir/guest-serial.log" ]]; then
      echo "--- QEMU guest serial log (tail) ---" >&2
      tail -n 100 "$work_dir/guest-serial.log" >&2 || true
    fi
    if [[ -f "$work_dir/qemu.log" ]]; then
      echo "--- QEMU host log ---" >&2
      cat "$work_dir/qemu.log" >&2 || true
    fi
  fi
  if [[ -n "$qemu_pid" ]] && kill -0 "$qemu_pid" 2>/dev/null; then
    kill "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
  fi
  if [[ -n "$postgres_proxy_pid" ]] && kill -0 "$postgres_proxy_pid" 2>/dev/null; then
    kill "$postgres_proxy_pid" 2>/dev/null || true
    wait "$postgres_proxy_pid" 2>/dev/null || true
  fi
  if [[ "$local_docker_acceptance" != 1 && -n "$postgres_fixture_cli" ]]; then
    if [[ "$postgres_marker_created" == true ]]; then
      "$postgres_fixture_cli" exec "$postgres_fixture_container_id" psql -X -q \
        -v ON_ERROR_STOP=1 -U cao_spec021_admin -d postgres \
        -c "DROP DATABASE IF EXISTS $postgres_marker_database" >/dev/null 2>&1 || true
    fi
    if [[ -n "$postgres_fixture_container_id" ]]; then
      local owner run_id container_name
      owner="$("$postgres_fixture_cli" inspect --format '{{index .Config.Labels "org.cao.owner"}}' \
        "$postgres_fixture_container_id" 2>/dev/null || true)"
      run_id="$("$postgres_fixture_cli" inspect --format '{{index .Config.Labels "org.cao.run"}}' \
        "$postgres_fixture_container_id" 2>/dev/null || true)"
      container_name="$("$postgres_fixture_cli" inspect --format '{{.Name}}' \
        "$postgres_fixture_container_id" 2>/dev/null || true)"
      if [[ "$owner" == spec021-runtime && "$run_id" == "$pg_run_id" \
        && "${container_name#/}" == "$postgres_fixture_container_name" ]]; then
        "$postgres_fixture_cli" rm --force "$postgres_fixture_container_id" >/dev/null 2>&1 || true
      fi
    fi
    if [[ -n "$postgres_fixture_network" ]]; then
      "$postgres_fixture_cli" network rm "$postgres_fixture_network_id" >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT

if [[ "$local_docker_acceptance" == 1 ]]; then
  : "${CAO_T097_PG_CONTAINER_ID:?Owned test PostgreSQL container is required}"
  : "${CAO_T097_PG_RUN_ID:?Owned test PostgreSQL run identity is required}"
  : "${CAO_T097_PG_IMAGE_ID:?Pinned test PostgreSQL image identity is required}"
  : "${CAO_T097_PG_NETWORK_NAME:?Private PostgreSQL network is required}"
  : "${CAO_T097_PG_UPSTREAM_HOST:?Private PostgreSQL network alias is required}"
  : "${CAO_T097_PG_UPSTREAM_PORT:?Private PostgreSQL port is required}"
  : "${CAO_T097_PG_MARKER_DB:?Run marker database is required}"
  : "${CAO_T097_PG_MARKER_NONCE:?Run marker nonce is required}"
  : "${CAO_T097_PG_PASSWORD_FILE:?Private mounted administrator secret is required}"
  pg_container_id="$CAO_T097_PG_CONTAINER_ID"
  pg_run_id="$CAO_T097_PG_RUN_ID"
  expected_pg_network_name="cao-spec021-test-net-${pg_run_id}"
  pg_image_id="$CAO_T097_PG_IMAGE_ID"
  pg_network_name="$CAO_T097_PG_NETWORK_NAME"
  pg_upstream_host="$CAO_T097_PG_UPSTREAM_HOST"
  pg_upstream_port="$CAO_T097_PG_UPSTREAM_PORT"
  pg_marker_database="$CAO_T097_PG_MARKER_DB"
  pg_marker_nonce="$CAO_T097_PG_MARKER_NONCE"
  pg_password_source="$CAO_T097_PG_PASSWORD_FILE"
  if [[ "$pg_image_id" != sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336 \
    || ! "$pg_container_id" =~ ^[a-f0-9]{64}$ \
    || ! "$pg_run_id" =~ ^[a-f0-9]{12}$ \
    || ! "$pg_marker_nonce" =~ ^[a-f0-9]{32}$ \
    || "$pg_marker_database" != "t097_marker_${pg_marker_nonce}" \
    || "$pg_network_name" != "$expected_pg_network_name" \
    || "$pg_upstream_host" != "cao-spec021-pg-${pg_run_id}" \
    || "$pg_upstream_port" != 5432 ]]; then
    echo "T097 PostgreSQL test-service identity is invalid" >&2
    exit 1
  fi
else
  postgres_fixture_cli="${CAO_T097_DOCKER_CLI:-docker}"
  if ! command -v "$postgres_fixture_cli" >/dev/null 2>&1; then
    echo "A local Docker CLI is required to provision the T097 PostgreSQL test service" >&2
    exit 1
  fi
  pg_run_id="$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  pg_marker_nonce="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  pg_image_ref="postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
  pg_image_id="sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
  pg_container_name="cao-spec021-pg-${pg_run_id}"
  postgres_fixture_container_name="$pg_container_name"
  pg_network_name="cao-spec021-net-${pg_run_id}"
  expected_pg_network_name="$pg_network_name"
  pg_marker_database="t097_marker_${pg_marker_nonce}"
  pg_password_source="$work_dir/admin-password"
  python3 - "$pg_password_source" <<'PY'
import os
import secrets
import sys

descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write(secrets.token_urlsafe(32) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
PY
  postgres_fixture_network_id="$("$postgres_fixture_cli" network create \
    --label org.cao.owner=spec021-runtime \
    --label "org.cao.run=$pg_run_id" "$pg_network_name")"
  postgres_fixture_network="$pg_network_name"
  pg_password_source="$(realpath "$pg_password_source")"
  container_id="$("$postgres_fixture_cli" run --detach \
    --name "$pg_container_name" \
    --network "$pg_network_name" \
    --label org.cao.owner=spec021-runtime \
    --label "org.cao.run=$pg_run_id" \
    --publish 127.0.0.1::5432 \
    --mount "type=bind,source=$pg_password_source,target=/run/secrets/postgres_password,readonly" \
    --tmpfs /var/lib/postgresql:rw,size=1g \
    --memory 2g --memory-swap 2g \
    --env POSTGRES_USER=cao_spec021_admin \
    --env POSTGRES_DB=postgres \
    --env POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password \
    --health-cmd 'pg_isready -U cao_spec021_admin -d postgres' \
    --health-interval 2s --health-timeout 3s --health-retries 60 \
    "$pg_image_ref" \
    postgres -c max_wal_size=128MB -c min_wal_size=32MB -c checkpoint_timeout=60s)"
  postgres_fixture_container_id="$container_id"
  pg_container_id="$("$postgres_fixture_cli" inspect --format '{{.Id}}' "$container_id")"
  actual_image_id="$("$postgres_fixture_cli" inspect --format '{{.Image}}' "$container_id")"
  actual_image_ref="$("$postgres_fixture_cli" inspect --format '{{.Config.Image}}' "$container_id")"
  actual_run_id="$("$postgres_fixture_cli" inspect --format '{{index .Config.Labels "org.cao.run"}}' "$container_id")"
  actual_owner="$("$postgres_fixture_cli" inspect --format '{{index .Config.Labels "org.cao.owner"}}' "$container_id")"
  if [[ "$actual_image_id" != "$pg_image_id" || "$actual_image_ref" != "$pg_image_ref" \
    || "$actual_run_id" != "$pg_run_id" || "$actual_owner" != spec021-runtime ]]; then
    echo "T097 PostgreSQL service did not match its pinned test identity" >&2
    exit 1
  fi
  for attempt in {1..90}; do
    state="$("$postgres_fixture_cli" inspect --format '{{.State.Health.Status}}' "$container_id")"
    if [[ "$state" == healthy ]]; then
      break
    fi
    if [[ "$state" == unhealthy ]]; then
      echo "T097 PostgreSQL test service became unhealthy" >&2
      exit 1
    fi
    sleep 1
  done
  [[ "$state" == healthy ]] || {
    echo "T097 PostgreSQL test service did not become healthy" >&2
    exit 1
  }
  pg_upstream_host=127.0.0.1
  actual_host_ip="$("$postgres_fixture_cli" inspect --format \
    '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostIp}}' "$container_id")"
  pg_upstream_port="$("$postgres_fixture_cli" inspect --format \
    '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$container_id")"
  [[ "$actual_host_ip" == 127.0.0.1 && "$pg_upstream_port" =~ ^[0-9]+$ ]] || {
    echo "T097 PostgreSQL service did not publish its loopback port" >&2
    exit 1
  }
fi

if [[ ! "$pg_container_id" =~ ^[a-f0-9]{64}$ \
  || ! "$pg_run_id" =~ ^[a-f0-9]{12}$ \
  || ! "$pg_marker_nonce" =~ ^[a-f0-9]{32}$ \
  || "$pg_marker_database" != "t097_marker_${pg_marker_nonce}" \
  || "$pg_image_id" != sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336 \
  || "$pg_network_name" != "$expected_pg_network_name" \
  || ! "$pg_upstream_port" =~ ^[0-9]+$ ]]; then
  echo "T097 PostgreSQL test-service identity is invalid" >&2
  exit 1
fi
test -f "$pg_password_source"
test ! -L "$pg_password_source"
test "$(stat -c '%a' "$pg_password_source")" = 600
if [[ "$local_docker_acceptance" != 1 ]]; then
  "$postgres_fixture_cli" exec "$pg_container_id" psql -X -q -v ON_ERROR_STOP=1 \
    -U cao_spec021_admin -d postgres -c \
    "CREATE DATABASE $pg_marker_database OWNER cao_spec021_admin" >/dev/null
  postgres_marker_created=true
  "$postgres_fixture_cli" exec -i "$pg_container_id" psql -X -q -v ON_ERROR_STOP=1 \
    -U cao_spec021_admin -d "$pg_marker_database" <<SQL >/dev/null
CREATE TABLE public.t097_service_identity (
  nonce text NOT NULL,
  container_id text NOT NULL,
  run_id text NOT NULL,
  image_id text NOT NULL
);
INSERT INTO public.t097_service_identity VALUES (
  '$pg_marker_nonce', '$pg_container_id', '$pg_run_id', '$pg_image_id'
);
REVOKE ALL ON public.t097_service_identity FROM PUBLIC;
SQL
else
  postgres_marker_created=true
fi
install -m 0600 "$pg_password_source" "$work_dir/admin-password"
PG_ADMIN_PASSWORD_COPY="$work_dir/admin-password" \
PG_CONTAINER_ID="$pg_container_id" \
PG_RUN_ID="$pg_run_id" \
PG_IMAGE_ID="$pg_image_id" \
PG_NETWORK_NAME="$pg_network_name" \
PG_MARKER_DB="$pg_marker_database" \
PG_MARKER_NONCE="$pg_marker_nonce" \
python3 - "$work_dir/postgres-metadata.json" <<'PY'
import json
import os
import sys
from pathlib import Path

metadata = {
    "service_mode": "cao-t097-owned-postgres-v1",
    "host": "10.0.2.2",
    "port": 5432,
    "database": "postgres",
    "user": "cao_spec021_admin",
    "password_file": "admin-password",
    "expected_server_major": 18,
    "storage_limit_bytes": 1024 * 1024 * 1024,
    "container_id": os.environ["PG_CONTAINER_ID"],
    "service_run_id": os.environ["PG_RUN_ID"],
    "image_id": os.environ["PG_IMAGE_ID"],
    "network_name": os.environ["PG_NETWORK_NAME"],
    "marker_database": os.environ["PG_MARKER_DB"],
    "marker_nonce": os.environ["PG_MARKER_NONCE"],
}
path = Path(sys.argv[1])
path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
path.chmod(0o600)
PY

python3 - "$pg_upstream_host" "$pg_upstream_port" \
  >"$work_dir/postgres-proxy.log" 2>&1 <<'PY' &
import select
import socket
import socketserver
import sys

upstream_host = sys.argv[1]
upstream_port = int(sys.argv[2])

class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            upstream = socket.create_connection((upstream_host, upstream_port), timeout=5)
        except OSError:
            return
        with upstream:
            self.request.setblocking(False)
            upstream.setblocking(False)
            sockets = (self.request, upstream)
            while True:
                readable, _, exceptional = select.select(sockets, (), sockets, 60)
                if exceptional or not readable:
                    return
                for source in readable:
                    target = upstream if source is self.request else self.request
                    try:
                        payload = source.recv(65536)
                    except OSError:
                        return
                    if not payload:
                        return
                    try:
                        target.sendall(payload)
                    except OSError:
                        return

class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

with ProxyServer(("127.0.0.1", 5432), ProxyHandler) as server:
    server.serve_forever()
PY
  postgres_proxy_pid=$!
  for attempt in {1..30}; do
    if ! kill -0 "$postgres_proxy_pid" 2>/dev/null; then
      cat "$work_dir/postgres-proxy.log" >&2
      echo "T097 PostgreSQL guest proxy failed to start" >&2
      exit 1
    fi
    if python3 - <<'PY' >/dev/null 2>&1
import socket
with socket.create_connection(("127.0.0.1", 5432), timeout=1):
    pass
PY
    then
      break
    fi
    sleep 0.1
  done
echo "T097 PostgreSQL proxy: guest 10.0.2.2:5432 -> owned private service"

if [[ "$host_prereq_cache" == 0 ]]; then
  sudo apt-get update
  sudo apt-get install --no-install-recommends -y \
    cloud-image-utils \
    openssh-client \
    ovmf \
    qemu-utils \
    qemu-system-x86
else
  for command_name in cloud-localds ssh-keygen qemu-img qemu-system-x86_64; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "pinned host prerequisite image is missing $command_name" >&2
      exit 1
    fi
  done
fi

uv_host_bin="$(command -v uv)"
uv --version
install -m 0755 "$uv_host_bin" "$work_dir/uv"

guest_disk="$work_dir/t097-guest.qcow2"
seed_image="$work_dir/seed.img"
if [[ "$host_prereq_cache" == 1 ]]; then
  : "${CAO_T097_GUEST_IMAGE_CACHE_PATH:?cache mode requires the pinned read-only guest image mount}"
  : "${CAO_T097_GUEST_IMAGE_CACHE_SHA256:?cache mode requires the pinned guest image SHA-256}"
  if [[ "$CAO_T097_GUEST_IMAGE_CACHE_PATH" != "$guest_image_container_path" \
    || "$CAO_T097_GUEST_IMAGE_CACHE_SHA256" != "$guest_image_sha256" \
    || ! -f "$CAO_T097_GUEST_IMAGE_CACHE_PATH" \
    || -L "$CAO_T097_GUEST_IMAGE_CACHE_PATH" ]]; then
    echo "T097 guest image cache binding is invalid" >&2
    exit 1
  fi
  guest_image="$CAO_T097_GUEST_IMAGE_CACHE_PATH"
else
  guest_image="$work_dir/stonking-server-cloudimg-amd64.img"
  curl --fail --location --silent --show-error --retry 3 \
    "$guest_image_url" --output "$guest_image"
fi
printf '%s  %s\n' "$guest_image_sha256" "$guest_image" | sha256sum --check
qemu-img create -f qcow2 -F qcow2 -b "$guest_image" "$guest_disk"
qemu-img resize "$guest_disk" 12G

ssh-keygen -q -t ed25519 -N '' -C t097-qemu-guest \
  -f "$work_dir/guest-ssh-key"
python3 - "$work_dir/guest-ssh-key.pub" "$work_dir/user-data" <<'PY'
from pathlib import Path
import sys

public_key = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not public_key.startswith("ssh-ed25519 "):
    raise SystemExit("expected an ephemeral Ed25519 SSH public key")
user_data = f"""#cloud-config
users:
  - default
  - name: runner
    gecos: T097 disposable QEMU acceptance runner
    groups: [sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - {public_key}
ssh_pwauth: false
"""
Path(sys.argv[2]).write_text(user_data, encoding="utf-8")
PY
printf 'instance-id: t097-%s\nlocal-hostname: t097-guest\n' \
  "${GITHUB_RUN_ID:-local}" >"$work_dir/meta-data"
cloud-localds "$seed_image" "$work_dir/user-data" "$work_dir/meta-data"

ovmf_code=/usr/share/OVMF/OVMF_CODE_4M.fd
ovmf_vars=/usr/share/OVMF/OVMF_VARS_4M.fd
test -r "$ovmf_code"
test -r "$ovmf_vars"
cp "$ovmf_vars" "$work_dir/OVMF_VARS.fd"

qemu-system-x86_64 \
  -name t097-qemu-acceptance \
  -machine q35 \
  -accel tcg,thread=multi \
  -cpu max \
  -smp "$qemu_vcpu_count" \
  -m "$qemu_memory_mb" \
  -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code" \
  -drive "if=pflash,format=raw,file=$work_dir/OVMF_VARS.fd" \
  -drive "if=virtio,format=qcow2,file=$guest_disk" \
  -drive "if=virtio,format=raw,readonly=on,file=$seed_image" \
  -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$guest_port-:22" \
  -device virtio-net-pci,netdev=net0 \
  -display none \
  -serial "file:$work_dir/guest-serial.log" \
  -monitor none >"$work_dir/qemu.log" 2>&1 &
qemu_pid=$!

ssh_args=(
  -i "$work_dir/guest-ssh-key"
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=no
  -o "UserKnownHostsFile=$work_dir/known_hosts"
)
guest_ready=false
for attempt in {1..300}; do
  if ssh "${ssh_args[@]}" -p "$guest_port" "$guest" true >/dev/null 2>&1; then
    guest_ready=true
    break
  fi
  sleep 2
done
if [[ "$guest_ready" != true ]]; then
  echo "QEMU guest did not become reachable over SSH" >&2
  exit 1
fi

ssh "${ssh_args[@]}" -p "$guest_port" "$guest" uname -a
ssh "${ssh_args[@]}" -p "$guest_port" "$guest" cat /etc/os-release

if [[ "$local_docker_acceptance" == 1 ]]; then
  printf 'T019 Docker runner kernel: '
  uname -a
  tar -czf "$work_dir/checkout.tar.gz" \
    --exclude='*/__pycache__' \
    --exclude='*/.pytest_cache' \
    --exclude='*/.mypy_cache' \
    --exclude='*/.ruff_cache' \
    --exclude='*/.venv' \
    --exclude='*/node_modules' \
    -C "$GITHUB_WORKSPACE" \
    pyproject.toml uv.lock README.md LICENSE src test scripts/hatch_build_tui_tag.py
else
  git -C "$GITHUB_WORKSPACE" archive --format=tar.gz \
    --output="$work_dir/checkout.tar.gz" \
    HEAD pyproject.toml uv.lock README.md LICENSE src test \
    scripts/hatch_build_tui_tag.py
fi
scp "${ssh_args[@]}" -P "$guest_port" \
  "$work_dir/checkout.tar.gz" "$guest:/home/runner/checkout.tar.gz"
scp "${ssh_args[@]}" -P "$guest_port" \
  "$work_dir/uv" "$guest:/home/runner/uv"
scp "${ssh_args[@]}" -P "$guest_port" \
  "$work_dir/postgres-metadata.json" "$guest:/home/runner/t097-postgres-metadata.json"
scp "${ssh_args[@]}" -P "$guest_port" \
  "$work_dir/admin-password" "$guest:/home/runner/t097-postgres-admin-password"

ssh "${ssh_args[@]}" -p "$guest_port" "$guest" \
  "env CAO_WORK_BROKER_ACCOUNT='$CAO_WORK_BROKER_ACCOUNT' T097_LOCAL_DOCKER_ACCEPTANCE='$local_docker_acceptance' bash -s" <<'REMOTE'
set -Eeuo pipefail

sudo apt-get update
sudo apt-get install --no-install-recommends -y \
  build-essential \
  curl \
  e2fsprogs \
  libcap-dev \
  meson \
  ninja-build \
  pkg-config \
  xz-utils

source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
bwrap_work="$(mktemp -d /tmp/t097-bwrap.XXXXXX)"
bwrap_source_root=/tmp/bwrap-source
bwrap_source_dir="$bwrap_source_root/bubblewrap-0.13.0"
postgres_guest_dir=""
runner_pg_test_dir=""
root_device_test_dir=""
casefold_mount_dir=""
casefold_image=""
scratch_bwrap_created=false
scratch_bwrap_root=/tmp/caos-exec/T097
scratch_bwrap_dir="$scratch_bwrap_root/bubblewrap-build"
scratch_bwrap_path="$scratch_bwrap_dir/bwrap"
cleanup_guest() {
  local status=$?
  local casefold_mount_active=false
  if [[ -n "$postgres_guest_dir" ]]; then
    sudo rm -f "$postgres_guest_dir/metadata.json" "$postgres_guest_dir/admin-password"
    sudo rmdir "$postgres_guest_dir" 2>/dev/null || true
  fi
  if [[ -n "$runner_pg_test_dir" \
    && "$runner_pg_test_dir" == /home/runner/t097-postgres-uid1000.* ]]; then
    rm -f -- "$runner_pg_test_dir/metadata.json" "$runner_pg_test_dir/admin-password"
    rmdir -- "$runner_pg_test_dir" 2>/dev/null || true
  fi
  if [[ -n "$root_device_test_dir" \
    && "$root_device_test_dir" == /root/t097-device-nodes.* ]]; then
    sudo rm -rf -- "$root_device_test_dir"
  fi
  if [[ -n "$casefold_mount_dir" \
    && "$casefold_mount_dir" == /tmp/t097-casefold.* ]]; then
    if mountpoint -q "$casefold_mount_dir"; then
      if ! sudo umount "$casefold_mount_dir"; then
        echo "Could not unmount the private T097 casefold filesystem" >&2
        status=1
      fi
    fi
    if mountpoint -q "$casefold_mount_dir"; then
      casefold_mount_active=true
      echo "Private T097 casefold filesystem is still mounted; preserving its backing image" >&2
      status=1
    else
      sudo rmdir "$casefold_mount_dir" 2>/dev/null || true
    fi
  fi
  if [[ "$casefold_mount_active" != true \
    && -n "$casefold_image" && "$casefold_image" == "$bwrap_work/t097-casefold.ext4" ]]; then
    rm -f -- "$casefold_image"
  fi
  if [[ "$scratch_bwrap_created" == true ]]; then
    sudo rm -rf -- "$scratch_bwrap_root" "$bwrap_source_root"
    sudo rmdir /tmp/caos-exec 2>/dev/null || true
  fi
  sudo rm -f /home/runner/t097-postgres-metadata.json /home/runner/t097-postgres-admin-password
  rm -f /home/runner/t097-pytest-host.log \
    /home/runner/t097-pytest-sigkill.log \
    /home/runner/t097-pytest-fd-theft.log \
    /home/runner/t097-pytest-scratch.log \
    /home/runner/t097-pytest-scratch-uid1000.log \
    /home/runner/t097-pytest-landlock.log \
    /home/runner/t097-pytest-device-nodes.log \
    /home/runner/t097-pytest-filesystem.log
  if [[ "$casefold_mount_active" != true ]]; then
    rm -rf "$bwrap_work"
  fi
  exit "$status"
}
trap cleanup_guest EXIT
curl --fail --location --silent --show-error --retry 3 \
  https://github.com/containers/bubblewrap/releases/download/v0.13.0/bubblewrap-0.13.0.tar.xz \
  --output "$bwrap_work/bubblewrap-0.13.0.tar.xz"
printf '%s  %s\n' "$source_sha256" "$bwrap_work/bubblewrap-0.13.0.tar.xz" \
  | sha256sum --check
if [[ -e /tmp/caos-exec || -L /tmp/caos-exec \
  || -e "$bwrap_source_root" || -L "$bwrap_source_root" ]]; then
  echo "T097 canonical Bubblewrap build paths already exist in the disposable guest" >&2
  exit 1
fi
scratch_bwrap_created=true
sudo install -d -o root -g root -m 0755 /tmp/caos-exec
sudo install -d -o root -g root -m 0755 "$scratch_bwrap_root"
install -d -o "$(id -u)" -g "$(id -g)" -m 0700 "$bwrap_source_root"
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0700 "$scratch_bwrap_dir"
tar -xJf "$bwrap_work/bubblewrap-0.13.0.tar.xz" -C "$bwrap_source_root"
meson setup "$scratch_bwrap_dir" "$bwrap_source_dir" \
  --prefix=/usr \
  --buildtype=release \
  -Dtests=false \
  -Dman=disabled \
  -Dselinux=disabled \
  -Dbash_completion=disabled \
  -Dzsh_completion=disabled
meson compile -C "$scratch_bwrap_dir"
sudo install -o root -g root -m 0755 "$scratch_bwrap_path" /usr/bin/bwrap
test "$(/usr/bin/bwrap --version)" = "bubblewrap 0.13.0"
test "$(stat -c '%u:%g:%a' /usr/bin/bwrap)" = "0:0:755"
sudo chown root:root \
  /tmp/caos-exec "$scratch_bwrap_root" "$scratch_bwrap_dir" "$scratch_bwrap_path"
sudo chmod 0755 \
  /tmp/caos-exec "$scratch_bwrap_root" "$scratch_bwrap_dir" "$scratch_bwrap_path"
test "$(stat -c '%u:%g:%a' /tmp/caos-exec)" = "0:0:755"
test "$(stat -c '%u:%g:%a' "$scratch_bwrap_root")" = "0:0:755"
test "$(stat -c '%u:%g:%a' "$scratch_bwrap_path")" = "0:0:755"
sha256sum /usr/bin/bwrap
gcc --version | head -n 1
meson --version

sudo sysctl -w kernel.yama.ptrace_scope=1
apparmor_userns=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [[ -e "$apparmor_userns" ]]; then
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
fi

workspace=/home/runner/t097-workspace
mkdir -p "$workspace"
tar -xzf /home/runner/checkout.tar.gz -C "$workspace"
chmod 0755 /home/runner/uv
binary_digest="$(sha256sum /usr/bin/bwrap | cut -d ' ' -f 1)"
allowlist_file="$workspace/src/cli_agent_orchestrator/backends/bubblewrap_backend.py"
if ! sed -n '/^_BWRAP_SHA256_ALLOWLIST: frozenset\[str\] = frozenset(/,/^)/p' \
  "$allowlist_file" | grep -Fq "\"$binary_digest\""; then
  echo "T097 built Bubblewrap digest is not in the source allowlist: $binary_digest" >&2
  exit 1
fi
echo "T097 canonical Bubblewrap digest is allowlisted: $binary_digest"
/home/runner/uv python install 3.12
cd "$workspace"
CAO_TUI_AUTOBUILD=0 /home/runner/uv sync --all-extras --dev --frozen

if [[ ! "$CAO_WORK_BROKER_ACCOUNT" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
  echo "invalid broker account name" >&2
  exit 1
fi
if getent passwd "$CAO_WORK_BROKER_ACCOUNT" >/dev/null; then
  echo "broker account already exists in the pinned guest image" >&2
  exit 1
fi
sudo useradd --create-home --system --shell /usr/sbin/nologin \
  "$CAO_WORK_BROKER_ACCOUNT"
workspace_group=cao-t097-workspace
sudo groupadd --system "$workspace_group"
sudo usermod --append --groups "$workspace_group" "$CAO_WORK_BROKER_ACCOUNT"
sudo chgrp -R "$workspace_group" "$workspace"
sudo chmod -R g+rX "$workspace"
for shared_dir in /home/runner/.local/share/uv /home/runner/.cache/uv; do
  if [[ -d "$shared_dir" ]]; then
    sudo chgrp -R "$workspace_group" "$shared_dir"
    sudo chmod -R g+rX "$shared_dir"
  fi
done
sudo chmod o+x /home/runner

broker_home="$(getent passwd "$CAO_WORK_BROKER_ACCOUNT" | cut -d: -f6)"
test -n "$broker_home"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- test -r "$workspace/pyproject.toml"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- test -x "$workspace/.venv/bin/python"

postgres_guest_dir="$broker_home/.local/share/cao/t097-postgres"
sudo install -d -o "$CAO_WORK_BROKER_ACCOUNT" -g "$CAO_WORK_BROKER_ACCOUNT" \
  -m 0700 "$postgres_guest_dir"
sudo install -o "$CAO_WORK_BROKER_ACCOUNT" -g "$CAO_WORK_BROKER_ACCOUNT" \
  -m 0600 /home/runner/t097-postgres-metadata.json "$postgres_guest_dir/metadata.json"
sudo install -o "$CAO_WORK_BROKER_ACCOUNT" -g "$CAO_WORK_BROKER_ACCOUNT" \
  -m 0600 /home/runner/t097-postgres-admin-password "$postgres_guest_dir/admin-password"
if [[ "$(id -un):$(id -u):$(id -g)" != "runner:1000:1000" ]]; then
  echo "the UID-1000 characterization must run as the isolated runner account" >&2
  exit 1
fi
runner_pg_test_dir="$(mktemp -d /home/runner/t097-postgres-uid1000.XXXXXX)"
if [[ "$runner_pg_test_dir" != /home/runner/t097-postgres-uid1000.* ]]; then
  echo "Cannot verify private UID-1000 PostgreSQL metadata directory" >&2
  exit 1
fi
install -m 0600 /home/runner/t097-postgres-metadata.json "$runner_pg_test_dir/metadata.json"
install -m 0600 /home/runner/t097-postgres-admin-password "$runner_pg_test_dir/admin-password"
test "$(stat -c '%u:%a' "$runner_pg_test_dir")" = "1000:700"
test "$(stat -c '%u:%a' "$runner_pg_test_dir/metadata.json")" = "1000:600"
test "$(stat -c '%u:%a' "$runner_pg_test_dir/admin-password")" = "1000:600"
sudo rm -f /home/runner/t097-postgres-metadata.json /home/runner/t097-postgres-admin-password
postgres_metadata_file="$postgres_guest_dir/metadata.json"
# The parent directory is mode 0700 and owned by the broker, so inspect the
# files as that account rather than asking the unprivileged runner to traverse it.
test "$(sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- stat -c '%u:%a' "$postgres_guest_dir/metadata.json")" = "$(id -u "$CAO_WORK_BROKER_ACCOUNT"):600"
test "$(sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- stat -c '%u:%a' "$postgres_guest_dir/admin-password")" = "$(id -u "$CAO_WORK_BROKER_ACCOUNT"):600"

PYTHONPATH="$workspace/src" "$workspace/.venv/bin/python" - <<'PY'
from cli_agent_orchestrator.services.work_process_landlock import _query_abi_version

abi = _query_abi_version()
print(f"QEMU guest Landlock ABI: {abi}")
if abi < 9:
    raise SystemExit(f"T097 guest requires Landlock ABI >= 9; observed {abi}")
PY

assert_pytest_summary() {
  local label="$1" expected="$2" log_file="$3" summary
  summary="$(grep -E '^[0-9]+ (passed|failed|error|skipped|xfailed|xpassed)' \
    "$log_file" | tail -n 1 || true)"
  if [[ ! "$summary" =~ ^${expected}[[:space:]]+passed([,[:space:]]|$) ]]; then
    echo "$label must report exactly $expected passed tests: ${summary:-missing summary}" >&2
    exit 1
  fi
  if [[ "$summary" =~ [1-9][0-9]*[[:space:]]+(failed|error|skipped|xfailed|xpassed|deselected)([,[:space:]]|$) ]]; then
    echo "$label must have no failures, skips, or deselected cases: $summary" >&2
    exit 1
  fi
}

expected_test_total=0

run_broker_pytest() {
  local label="$1" expected="$2" log_file="$3"
  shift 3
  echo "Running $label as $CAO_WORK_BROKER_ACCOUNT (expecting $expected passing tests)"
  if ! sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- env \
    HOME="$broker_home" \
    CAO_WORK_BROKER_ACCOUNT="$CAO_WORK_BROKER_ACCOUNT" \
    T097_REQUIRE_HOST_ACCEPTANCE=1 \
    T097_TEST_WORKER_TIMEOUT_SECONDS=45 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$workspace/src" \
    CAO_TEST_POSTGRES_METADATA_FILE="$postgres_metadata_file" \
    "$workspace/.venv/bin/python" -m pytest -q -o addopts= \
      "$@" --no-cov --tb=short -rA -p no:cacheprovider 2>&1 | tee "$log_file"; then
    echo "$label pytest invocation failed; see $log_file" >&2
    exit 1
  fi
  assert_pytest_summary "$label" "$expected" "$log_file"
  expected_test_total=$((expected_test_total + expected))
}

run_uid1000_pytest() {
  local label="$1" expected="$2" log_file="$3"
  shift 3
  if [[ "$(id -un):$(id -u):$(id -g)" != "runner:1000:1000" ]]; then
    echo "$label requires the isolated runner account at UID/GID 1000" >&2
    exit 1
  fi
  test -n "$runner_pg_test_dir"
  test "$(stat -c '%u:%a' "$runner_pg_test_dir")" = "1000:700"
  test "$(stat -c '%u:%a' "$runner_pg_test_dir/metadata.json")" = "1000:600"
  test "$(stat -c '%u:%a' "$runner_pg_test_dir/admin-password")" = "1000:600"
  echo "Running $label as runner UID/GID 1000 (expecting $expected passing tests)"
  if ! PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$workspace/src" \
    CAO_TEST_POSTGRES_METADATA_FILE="$runner_pg_test_dir/metadata.json" \
    "$workspace/.venv/bin/python" -m pytest -q -o addopts= \
      "$@" --no-cov --tb=short -rA -p no:cacheprovider 2>&1 | tee "$log_file"; then
    echo "$label pytest invocation failed; see $log_file" >&2
    exit 1
  fi
  assert_pytest_summary "$label" "$expected" "$log_file"
  expected_test_total=$((expected_test_total + expected))
}

run_root_pytest() {
  local label="$1" expected="$2" log_file="$3" root_home="$4" root_tmp="$5"
  shift 5
  echo "Running $label as root in its private guest home (expecting $expected passing tests)"
  if ! sudo -n env \
    HOME=/root \
    CAO_HOME_DIR="$root_home" \
    TMPDIR="$root_tmp" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$workspace/src" \
    CAO_TEST_POSTGRES_METADATA_FILE="$root_device_test_dir/metadata.json" \
    "$workspace/.venv/bin/python" -m pytest -q -o addopts= \
      "$@" --no-cov --tb=short -rA -p no:cacheprovider 2>&1 | tee "$log_file"; then
    echo "$label pytest invocation failed; see $log_file" >&2
    exit 1
  fi
  assert_pytest_summary "$label" "$expected" "$log_file"
  expected_test_total=$((expected_test_total + expected))
}

run_broker_pytest \
  "T097 real host acceptance" 8 /home/runner/t097-pytest-host.log \
  test/integration/t097/test_host_acceptance.py \
  test/integration/t097/test_proxy_concurrency.py \
  test/integration/t097/test_sibling_isolation.py -m t097_host

run_broker_pytest \
  "T097 restored SIGKILL proxy acceptance" 2 \
  /home/runner/t097-pytest-sigkill.log \
  test/integration/t097/test_proxy_sigkill_t097_restored.py -m t097_host

run_broker_pytest \
  "T097 same-UID post-exec FD-theft acceptance" 1 \
  /home/runner/t097-pytest-fd-theft.log \
  test/integration/t097/test_same_uid_fd_theft_t097_restored.py -m t097_host

scratch_manifest="$workspace/test/integration/t097/scratch-selectors.txt"
python3 - "$workspace" "$scratch_manifest" <<'PY'
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
manifest = Path(sys.argv[2])
selectors = manifest.read_text(encoding="utf-8").splitlines()
if len(selectors) != 63 or len(set(selectors)) != 63:
    raise SystemExit("T097 scratch selector manifest must contain 63 unique cases")
for selector in selectors:
    if not selector.startswith("test/") or selector.count("::") != 1:
        raise SystemExit(f"invalid T097 scratch selector: {selector!r}")
    test_file = selector.split("::", 1)[0]
    if not (workspace / test_file).is_file():
        raise SystemExit(f"T097 scratch selector references a missing file: {test_file}")
PY
mapfile -t scratch_selectors < "$scratch_manifest"
scratch_uid1000_selector='test/security/test_work_bubblewrap_sibling_userns.py::test_same_host_uid_userns_sibling_cannot_inspect_nondumpable_target'
scratch_broker_selectors=()
scratch_uid1000_count=0
for selector in "${scratch_selectors[@]}"; do
  if [[ "$selector" == "$scratch_uid1000_selector" ]]; then
    scratch_uid1000_count=$((scratch_uid1000_count + 1))
  else
    scratch_broker_selectors+=("$selector")
  fi
done
if [[ "${#scratch_broker_selectors[@]}" -ne 62 || "$scratch_uid1000_count" -ne 1 ]]; then
  echo "T097 scratch partition must contain 62 broker cases and the exact UID-1000 case" >&2
  exit 1
fi
run_broker_pytest \
  "T097 scratch Bubblewrap acceptance (dedicated broker)" 62 \
  /home/runner/t097-pytest-scratch.log \
  "${scratch_broker_selectors[@]}"
run_uid1000_pytest \
  "T097 exact UID-1000 sibling characterization" 1 \
  /home/runner/t097-pytest-scratch-uid1000.log \
  "$scratch_uid1000_selector"

run_broker_pytest \
  "T097 Landlock ABI-9 pathname socket acceptance" 1 \
  /home/runner/t097-pytest-landlock.log \
  test/services/test_work_process_landlock.py::test_linux_resolve_unix_allows_listed_and_denies_unlisted_pathname_sockets

root_device_test_dir="$(sudo mktemp -d /root/t097-device-nodes.XXXXXX)"
if [[ "$root_device_test_dir" != /root/t097-device-nodes.* ]]; then
  echo "Cannot verify private root test directory" >&2
  exit 1
fi
sudo install -d -o root -g root -m 0700 \
  "$root_device_test_dir/cao-home" "$root_device_test_dir/tmp"
sudo install -o root -g root -m 0600 \
  "$postgres_guest_dir/metadata.json" "$root_device_test_dir/metadata.json"
sudo install -o root -g root -m 0600 \
  "$postgres_guest_dir/admin-password" "$root_device_test_dir/admin-password"
test "$(sudo stat -c '%u:%a' "$root_device_test_dir/metadata.json")" = "0:600"
test "$(sudo stat -c '%u:%a' "$root_device_test_dir/admin-password")" = "0:600"
run_root_pytest \
  "T097 device-node snapshot acceptance" 2 \
  /home/runner/t097-pytest-device-nodes.log \
  "$root_device_test_dir/cao-home" "$root_device_test_dir/tmp" \
  'test/security/test_work_bubblewrap_runtime_snapshot.py::test_snapshot_rejects_device_nodes_when_runner_can_create_them[character]' \
  'test/security/test_work_bubblewrap_runtime_snapshot.py::test_snapshot_rejects_device_nodes_when_runner_can_create_them[block]'
sudo rm -rf -- "$root_device_test_dir"
root_device_test_dir=""

casefold_manifest="$workspace/test/integration/t097/extra-filesystem-selectors.txt"
python3 - "$workspace" "$casefold_manifest" <<'PY'
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
manifest = Path(sys.argv[2])
selectors = manifest.read_text(encoding="utf-8").splitlines()
if len(selectors) != 3 or len(set(selectors)) != 3:
    raise SystemExit("T097 extra filesystem selector manifest must contain 3 unique cases")
for selector in selectors:
    if not selector.startswith("test/") or selector.count("::") != 1:
        raise SystemExit(f"invalid T097 extra filesystem selector: {selector!r}")
    test_file = selector.split("::", 1)[0]
    if not (workspace / test_file).is_file():
        raise SystemExit(f"T097 extra filesystem selector references a missing file: {test_file}")
PY
mapfile -t casefold_selectors < "$casefold_manifest"

# The guest owns this disposable ext4 image. Its casefold directories provide
# the same filesystem behavior the host-only tests require without changing
# the host filesystem or treating a skipped test as a pass.
casefold_image="$bwrap_work/t097-casefold.ext4"
casefold_mount_dir="$(sudo mktemp -d /tmp/t097-casefold.XXXXXX)"
if [[ "$casefold_mount_dir" != /tmp/t097-casefold.* ]]; then
  echo "Cannot verify private casefold mount directory" >&2
  exit 1
fi
truncate -s 128M "$casefold_image"
mkfs.ext4 -F -O casefold -E encoding=utf8 "$casefold_image" >/dev/null
sudo mount -o loop "$casefold_image" "$casefold_mount_dir"
test "$(findmnt -n -o FSTYPE -T "$casefold_mount_dir")" = ext4
casefold_parent="$casefold_mount_dir/pytest-parent"
casefold_basetemp="$casefold_parent/pytest-base"
sudo install -d -o "$CAO_WORK_BROKER_ACCOUNT" -g "$CAO_WORK_BROKER_ACCOUNT" \
  -m 0700 "$casefold_parent"
sudo chattr +F "$casefold_parent"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- mkdir "$casefold_parent/CaseProbe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- test -d "$casefold_parent/caseprobe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- rmdir "$casefold_parent/CaseProbe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- mkdir "$casefold_parent/TreeProbe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- mkdir "$casefold_parent/TreeProbe/CaseProbe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- test -d "$casefold_parent/TreeProbe/caseprobe"
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- rm -rf -- "$casefold_parent/TreeProbe"
run_broker_pytest \
  "T097 dead-monitor recovery and ext4 casefold acceptance" 3 \
  /home/runner/t097-pytest-filesystem.log \
  --basetemp "$casefold_basetemp" \
  "${casefold_selectors[@]}"

run_broker_pytest \
  "T097 bound-content and process-identity acceptance" 7 \
  /home/runner/t097-pytest-bound-content.log \
  test/security/test_work_bubblewrap_bound_content.py::test_bound_v2_launch_uses_one_owned_stage_and_persists_identity_before_go \
  test/security/test_work_bubblewrap_bound_content.py::test_recorded_bound_launch_activates_private_proxy_only_after_ack_and_durable_setup \
  test/security/test_work_bubblewrap_bound_content.py::test_recorded_bound_launch_reports_uncertainty_after_go_write \
  test/security/test_work_bubblewrap_bound_content.py::test_recorded_bound_launch_runs_real_worker_after_pending_setup \
  test/security/test_work_bubblewrap_process_identity.py::test_callback_without_process_identity_never_releases_worker \
  test/security/test_work_bubblewrap_process_identity.py::test_identity_persistence_error_withholds_worker_release \
  test/security/test_work_bubblewrap_process_identity.py::test_parent_persists_exact_process_identity_before_worker_release

run_broker_pytest \
  "T098 current-source PostgreSQL Work launch dispatch" 4 \
  /home/runner/t097-pytest-t098.log \
  test/integration/t098/test_work_launch_dispatch.py::test_gateway_runs_scratch_worker_through_bubblewrap_and_supervisor \
  test/integration/t098/test_work_launch_dispatch.py::test_double_fork_descendants_can_exec_only_the_bound_static_elf \
  test/integration/t098/test_work_launch_dispatch.py::test_go_authority_revocation_leaves_no_worker_effect_or_redelivery \
  test/integration/t098/test_work_launch_dispatch.py::test_uncertain_cleanup_is_reconciled_without_process_redelivery

if [[ "$expected_test_total" -ne 91 ]]; then
  echo "T097/T098 QEMU acceptance must select exactly 91 passing tests (declared $expected_test_total)" >&2
  exit 1
fi
REMOTE
