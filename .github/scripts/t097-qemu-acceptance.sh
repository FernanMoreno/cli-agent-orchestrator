#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?GitHub workspace is required}"
: "${RUNNER_TEMP:?GitHub runner temp directory is required}"
: "${CAO_WORK_BROKER_ACCOUNT:?Set the repository variable CAO_WORK_BROKER_ACCOUNT}"

local_docker_acceptance="${CAO_T019_LOCAL_DOCKER_ACCEPTANCE:-0}"
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
readonly bubblewrap_source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
readonly guest_port=22223
readonly guest=runner@127.0.0.1

work_dir="$(mktemp -d "${RUNNER_TEMP}/t097-qemu.XXXXXX")"
qemu_pid=""
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
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT

sudo apt-get update
sudo apt-get install --no-install-recommends -y \
  cloud-image-utils \
  openssh-client \
  ovmf \
  qemu-utils \
  qemu-system-x86

uv_host_bin="$(command -v uv)"
uv --version
install -m 0755 "$uv_host_bin" "$work_dir/uv"

guest_image="$work_dir/stonking-server-cloudimg-amd64.img"
guest_disk="$work_dir/t097-guest.qcow2"
seed_image="$work_dir/seed.img"
curl --fail --location --silent --show-error --retry 3 \
  "$guest_image_url" --output "$guest_image"
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
  -smp 4 \
  -m 4096 \
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
for attempt in {1..120}; do
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

ssh "${ssh_args[@]}" -p "$guest_port" "$guest" \
  "env CAO_WORK_BROKER_ACCOUNT='$CAO_WORK_BROKER_ACCOUNT' bash -s" <<'REMOTE'
set -Eeuo pipefail

sudo apt-get update
sudo apt-get install --no-install-recommends -y \
  build-essential \
  curl \
  libcap-dev \
  meson \
  ninja-build \
  pkg-config \
  xz-utils

source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
bwrap_work="$(mktemp -d /tmp/t097-bwrap.XXXXXX)"
trap 'rm -rf "$bwrap_work"' EXIT
curl --fail --location --silent --show-error --retry 3 \
  https://github.com/containers/bubblewrap/releases/download/v0.13.0/bubblewrap-0.13.0.tar.xz \
  --output "$bwrap_work/bubblewrap-0.13.0.tar.xz"
printf '%s  %s\n' "$source_sha256" "$bwrap_work/bubblewrap-0.13.0.tar.xz" \
  | sha256sum --check
tar -xJf "$bwrap_work/bubblewrap-0.13.0.tar.xz" -C "$bwrap_work"
meson setup "$bwrap_work/build" "$bwrap_work/bubblewrap-0.13.0" \
  --prefix=/usr \
  --buildtype=release \
  -Dtests=false \
  -Dman=disabled \
  -Dselinux=disabled \
  -Dbash_completion=disabled \
  -Dzsh_completion=disabled
meson compile -C "$bwrap_work/build"
sudo install -o root -g root -m 0755 "$bwrap_work/build/bwrap" /usr/bin/bwrap
test "$(/usr/bin/bwrap --version)" = "bubblewrap 0.13.0"
test "$(stat -c '%u:%g:%a' /usr/bin/bwrap)" = "0:0:755"
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

PYTHONPATH="$workspace/src" "$workspace/.venv/bin/python" - <<'PY'
from cli_agent_orchestrator.services.work_process_landlock import _query_abi_version

abi = _query_abi_version()
print(f"QEMU guest Landlock ABI: {abi}")
if abi < 9:
    raise SystemExit(f"T097 guest requires Landlock ABI >= 9; observed {abi}")
PY

log_file=/home/runner/t097-pytest.log
sudo -n -u "$CAO_WORK_BROKER_ACCOUNT" -- env \
  HOME="$broker_home" \
  CAO_WORK_BROKER_ACCOUNT="$CAO_WORK_BROKER_ACCOUNT" \
  T097_REQUIRE_HOST_ACCEPTANCE=1 \
  T097_TEST_WORKER_TIMEOUT_SECONDS=45 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$workspace/src" \
  "$workspace/.venv/bin/python" -m pytest -q \
    test/integration/t097 -m t097_host \
    --no-cov --tb=short -rA -p no:cacheprovider | tee "$log_file"

summary="$(grep -E '^[0-9]+ (passed|failed|error|skipped|xfailed|xpassed)' "$log_file" | tail -n 1)"
test -n "$summary" || { echo "Pytest summary missing" >&2; exit 1; }
if [[ "$summary" != 8\ passed* ]]; then
  echo "T097 QEMU acceptance must pass all eight cases: $summary" >&2
  exit 1
fi
if grep -Eq '(^|, )[1-9][0-9]* (skipped|xfailed|xpassed)' <<<"$summary"; then
  echo "T097 QEMU acceptance must have no skipped or expected-failure cases: $summary" >&2
  exit 1
fi
REMOTE
