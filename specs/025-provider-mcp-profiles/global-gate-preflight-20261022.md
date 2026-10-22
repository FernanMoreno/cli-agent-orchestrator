# Global gate R4 preparation and blocked admission — 2026-10-22

Status: preparation reviewed; actual global validation **not started**. This does not close historical NFR, coverage or provider gates.

After native-v1 R12 stopped at its unchanged memory admission and cleaned its owned state, root prepared a fresh private global runner bound to published main `73bb0c0b286ac75bc361c1dc43d4b6184fe87cdc`. Changes from closed R3 are limited to HEAD, source-tree digest, private ext4 output root and the current verified PostgreSQL metadata path. All functions remain AST-identical. The source digest includes tracked `src`, `test`, `pyproject.toml`, `.coverage-baseline.json` and `cao_mcp_apps`, using Git modes, file content and raw symlink text without following link targets.

The six temporary-Git-fixture control groups passed: historical symlink rejection RED, regular/internal/external/dangling handling, exact link-text hashing without target reads, regular content hashing, type/mode/path guards and parent-symlink refusal. These are pure preparation controls, not project tests. Claude independently approved the final source/diff in read mode; it ran no commands and did not compute hashes. Root verified the runner/base/control hashes, source binding, pinned Python/Node/local Vitest and owned PostgreSQL identity/metadata checks before reaching the resource gate.

All three preflight attempts stopped at the unchanged **6,144 MiB** resource predicate before opening either execution gate. The second followed a fresh7,539 MiB observation after other processes changed, but memory had dropped below the floor by the resource check. A third followed a fresh7,289 MiB observation after the observed Angular tests exited, but again stopped at the resource check. The exact memory value inside each failed predicate was not recorded. Later samples ranged from2,661 to5,175 MiB; these are observations, not the failed-predicate measurements. The first attempt's content-free blocked receipt is retained privately. No project pytest, cold NFR, Vitest, ratchet, application service, QEMU, real provider or historical reconciliation job was launched by R4. Its execution gates remain false and there is no actual-run directory or opening receipt.

Read-only process inspection found large Angular test processes outside CAOS; none was stopped or modified. No inactive CAOS/pytest directory above50 MiB suitable for the scoped tmpfs inspection was found. Previous verified evidence relocations remain as recorded; no historical artifact was moved or deleted here.

The prepared order and criteria remain unchanged:

1. Three independent cold covered Python processes, `-n0`, exact ten-store/parse benchmark and `<1.0s`, stop on first failure.
2. Full `test/`, eight workers/loadfile, removing only the default marker exclusion and retaining all coverage with the87% floor.
3. Local installed Apps Vitest with coverage, then the existing Python87%/Apps90% ratchet.

Every stage requires the memory floor, unchanged source binding and ext4 outputs. PostgreSQL stays the same existing container/image with autostart disabled. Child environments isolate HOME/tmp and disable live-provider, host/QEMU and separate operator acceptance lanes. No dependency install, operator credential mutation, model switch, threshold reduction or coverage exclusion was introduced.

Closed R4 runner SHA256 `82d6362f35d3d5db581c69388136a36a0cc812b16703749270c3b4c6b6ef1687`; source-tree digest `826f909b3723b3aaedc82219b66a2976633901dbcf28d83133514d89d6429c4f`. After this documentation commit, repin HEAD before any actual run. The original1.010s/2.200s NFR failures and86.913605% coverage result remain open. T014/global gates and native T060/T068 remain pending.
