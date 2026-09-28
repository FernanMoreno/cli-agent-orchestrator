# T097 formal composition review — 2026-09-26

**Verdict: FAIL for T097 acceptance; T097 and T019 remain `[ ]`.** The tmux ABA review found name-based destructive Work cleanup unsafe. The corrective source delta rejects Work rollback, `kill_session`, and `kill_window` until a server-incarnation-fenced identity exists. The target, ordinary input, and supervisor environment fences have bounded evidence. This reviewer inspected source and the supplied reports; no provider or operator database was exercised here.

## Changed and adjacent boundaries

- `WorkAdmission` derives the restriction from the effective contract, commits an attempt and supplies a fresh effect guard. `WorkBackendView` binds the exact durable terminal ID and session/window before protected transport; the guard rereads `work_attempts` and `terminals` on one SQLite snapshot. Launch and agent-step derive their targets from the existing frozen payload, while ordinary terminal input and key routes check durable Work ownership, including across restart.
- The Linux `BubblewrapWorkBackend` remains unregistered and rejects every Work contract after version probing. `WORK_BACKENDS` is empty. Tmux/Herdr do not acquire Work capability. The official [Bubblewrap advisory](https://github.com/containers/bubblewrap/security/advisories/GHSA-pxhw-h44j-8pfx) identifies versions below 0.12.0 as affected by a symlink traversal during sandbox setup; the installed 0.11.1 is outside the planned gate.
- `WorkProcessSupervisor` provides one-shot PID namespace lifecycle containment and excludes unrelated inherited descriptors. Its restart test proves no automatic redelivery or blind cleanup replay, but no durable process reattachment after service restart. `WorkMcpProxy` rejects before secret lookup, issue claim or socket creation because same-UID sibling isolation is unproved.

## Evidence and residual gates

- The target-binding RED harness recorded five behavioral failures before its fix. The builder's targeted gate recorded 110 passed; delivery integration recorded 28 passed. Root verified SHA-256 equality on **28 affected source/test paths** between the worktree and `/home/felni/.cache/caos-t097-ext4-shadow`, then ran the earlier consolidated suite: **164 passed, four warnings, zero skipped, exit 0 in 83.65 s**, recorded in `/tmp/caos-exec/T097/final-affected-pytest.log`. Both Bubblewrap characterization files ran with a verified scratch 0.13.0 build; their 12 executed probes characterize bypasses and do not accept the backend. This 164-pass snapshot predates the command-token preflight below.
- The earlier one-shot rollback recorded an exact session/window **name** returned by the live Work view. Its losing-insert test used SQLite and an in-memory backend; it did not prove ownership of a tmux session incarnation. A replacement could reuse the name before rollback, and a stale durable row could authorize ordinary `kill_session` or `kill_window` against a replacement. A private tmux 3.6 server returned `$0`; after `kill-server`, a new server returned `$0` again. Thus `$session_id` without a tmux server-incarnation fence is insufficient. Current `WorkBackendView` and `WorkAdmission` reject all three destructive Work effects and retain failed launch state for reconciliation. The legacy terminal cleanup path is unchanged.
- A scratch create-then-hydration-failure probe recorded **RED: one failure**. It simulated `new_session` creating `$1` and then failing to hydrate the libtmux object; the existing tmux client attempted cleanup by reusable name while the resource remained. No GREEN or verified recovery is claimed for that client path. If backend creation raises before returning an exact resource handle, Work cannot construct safe teardown authority; it must stay fail-closed and reconcile.
- The supervisor environment follow-up removes caller-supplied environment and launches its monitor with an empty environment. Its focused snapshot result was seven passed; the consolidated suite above includes the change. This does not integrate a usable Work sandbox.
- A 2026-09-26 rootless mapping probe ran as host UID **1000**. `newuidmap` was absent, and `/proc/self/uid_map` exposed only `0 1000 1`. `unshare --user --map-auto` exited **127** because `newuidmap` was not found. A nested `unshare --user --map-root-user` followed by `unshare --user --setgroups=deny --map-users=0:1:1` exited **1** with `uid_map: Operation not permitted`; `setresuid(1,1,1)` in the one-entry namespace failed with **EINVAL**. This host probe supplies no distinct per-attempt **host** UID for the proxy boundary. The official [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html) explains that unmapped UIDs are exposed as the overflow UID in most user-space views (default **65534**) and that only mapped IDs can be used by UID-changing calls. An overflow display value is not evidence of a distinct mapped host UID.
- The new `BubblewrapWorkBackend` command-token screen is **syntax-only**. It rejects shell fragments, relative executable names, nonnormalized paths, and leading `//` before `_probe_bubblewrap` can run. A canonical absolute token such as `/usr/bin/python3` passes only this shape check, reaches the version probe, and still receives the generic fail-closed `no command contract is currently supportable` rejection. The screen establishes neither executable identity nor descendant command confinement. Root's independent ext4 gate ran `/mnt/c/users/ferna/onedrive/escritorio/caos/.venv/bin/pytest --no-cov -q test/backends/test_bubblewrap_backend.py test/backends/test_work_enforcement.py test/security/test_work_backend_registration_gate.py test/security/test_work_bubblewrap_adversarial.py test/security/test_work_bubblewrap_open_questions.py` in `/home/felni/.cache/caos-t097-ext4-shadow`: **107 passed, zero skipped in 9.54 s**. The fresh composition check passed with **274 files, 1,016 dependencies, four kept, zero broken**. These are bounded rejection and characterization results; T097 is not accepted.
- The bounded network-preflight increment rejects a nonempty
  `contract.network` with `UnsupportedWorkEnforcement` after command-token
  syntax validation and before `_probe_bubblewrap`; otherwise valid contracts
  still reach the unconditional fail-closed rejection. Its RED test observed
  the fake `--version` executable creating a marker; GREEN asserts the
  expected `non-empty network contracts have no supported grammar` reason and
  no marker. Independent source/diff review: **PASS** for this ordering and
  bounded no-subprocess claim. The builder documented **21 passed** for the
  focused Bubblewrap module; root reported **108 passed** for the combined
  backend/security suite. Neither result establishes network isolation or
  authorizes backend registration.
- A test-only supervisor characterization covers `UNCERTAIN` followed by
  natural exit: `wait` observes `TERMINATED`, return code 0 and the child
  result, then releases this supervisor's block. No production source was
  added for this behavior. The current checkout run of
  `test/services/test_work_process_supervisor.py` recorded **7 passed**.
  The ext4-shadow attempt at the full supervisor suite instead produced
  **four errors before collection** from
  `PermissionError: /mnt/c/DumpStack.log.tmp`; it is not passing evidence.
  The fresh composition check passed: **274 files, 1,016 dependencies,
  four kept, zero broken**. Restart reattachment and durable cleanup ownership
  remain unproved.
- T097 still lacks a supported executable-token contract and integrated launcher, enforced filesystem/network/IPC policy, sibling-safe per-attempt proxy identity and FD boundary, real sandbox launch and continuation tests, a tmux server-incarnation fence, durable cleanup ownership after restart, and demonstrated partial-failure teardown. The host's Bubblewrap 0.11.1 remains below the upstream 0.12.0 security floor; the verified scratch 0.13.0 build is probe evidence only. The Bubblewrap preflight rejects all contracts, and the proxy cannot prove same-host-UID sibling isolation. T019 also retains its own credential, child/handoff and ACK gates; T035 retains public ingress.

The fresh composition check after the syntax screen reported **274 files, 1,016 dependencies, four contracts kept, zero broken**. The earlier T097 diff check exited 0 and its scoped whitespace audit passed **31/31**; those checks preceded the syntax screen. No contract test against a real provider or enabled Work backend was applicable to the fail-closed slice. No backend was registered, no operator Work database was used, and no public ingress was enabled. Graphify leads for admission and backend effects were checked against source. The graph predates `WorkProcessSupervisor`, `WorkLaunchRuntime`, and `WorkProvisioning`; refreshing it on this broad unrelated dirty tree is deferred to T089.

## Test-only characterization follow-up — 2026-09-26

**PASS for this bounded test increment; FAIL for T097 acceptance. T097 and
T019 remain `[ ]`.** Independent source review found changes confined to
`test/services/test_work_process_supervisor.py` and
`test/security/test_work_bubblewrap_noexec_inventory.py`; production code did
not change. The supervisor tests inject partial pidfd acquisition, an
uncertain recovery result, and reattach cleanup. They check both recovered
termination and the case where the original attempt remains `UNCERTAIN` while
its partial descriptor closes only after matching reattach exit proof. This
characterizes test cleanup; it does not add a durable production cleanup owner.

The private Bubblewrap 0.13.0 inventory asserts topmost `rw+noexec` mounts
for workspace, `/tmp`, and `/dev/shm`, a `ro+exec` runner, no `rw+exec` mount,
and six `/proc/self/fd/3` probes without marker creation. Three direct alias
execs are denied and three loader alias invocations fail to map their ELF.
Reported fresh runs: supervisor module **20 passed in 11.84 s**; a 12-module
consolidated suite **151 passed in 23.43 s**; isolated inventory **1 passed
in 4.49 s**, with `rw+exec=[]` and all six alias outcomes as asserted. The
composition check reported **275 files, 1,017 dependencies, four kept, zero
broken**; the two test files passed the scoped whitespace audit. These
results are bounded to the observed host, scratch binary, fixtures, and
test-only mount layout. They do not establish the executable identity or
digest contract, immutable staging, production Landlock inheritance, complete
FD admission, proxy sibling isolation, real Work launch/continuation, or
durable recovery and teardown. `WORK_BACKENDS` remains empty and Bubblewrap
preflight still rejects every contract before a Work effect. No provider,
operator database, or commit was used. Graphify's existing graph was checked
against source; a test-only increment does not require a refresh, and the
earlier structural refresh decision remains deferred to T089.

## Descriptive ELF, Landlock, and staging increment — 2026-09-26

**PASS for this bounded auxiliary increment; FAIL for T097 acceptance. T097
and T019 remain `[ ]`.** Independent source review covered the v2 contract,
ELF parser, Landlock helper, sealed staging helper, and their four test files.
`EffectiveWorkContractV2` is separate from the persisted v1 binding and
describes only static x86_64/ELF64/little identities with canonical command
tokens and exact command mapping. The fixed v1 canonical hash remains
unchanged. The parser requires a file-backed executable `PT_LOAD` containing
the entry point and rejects `PT_DYNAMIC`/`PT_INTERP`. Staging verifies a byte
snapshot against the identity, seals an executable memfd, checks readback, and
confirms its `O_PATH` descriptor names the same object. Landlock adds
path-based `FS_EXECUTE` rules inherited by descendants. These helpers have no
callers in Work admission or supervisor execution and grant no Work authority.

Two real-kernel probes define the unresolved execution boundary. Adding the
sealed memfd's `O_PATH` as a Landlock path rule returned **EBADFD=77**. With a
Landlock rule for a regular file and the current seccomp filter, a separately
inherited, unlisted memfd executed through `/proc/self/fd` and returned
**43**. The test deliberately passed that descriptor; its success is bypass
evidence, not proof of a safe launcher. The supervisor still calls
`os.execvpe(argv[0], ...)`, `WORK_BACKENDS` is empty, and Bubblewrap preflight
rejects every contract. The installed Bubblewrap 0.11.1 is below the required
0.12.0. The earlier rootless UID probe found `newuidmap` absent; distinct
host UID mapping and same-UID proxy isolation remain unproved. An integrated
launcher, inherited-FD boundary, filesystem/network/IPC enforcement, proxy
isolation, tmux fence, and durable cleanup are still required.

Root's final reported gate after the conditional API docstrings changed:
an explicit 18-module suite **304 passed, four warnings, zero skipped,
exit 0 in 36.64 s**; staging focus **10 passed, zero skipped**; Landlock focus
**8 passed**; Black `--check` passed on all eight paths; trailing-whitespace
search had no matches; composition check reported **278 files, 1,020
dependencies, four kept, zero broken**. No provider, operator database, or
commit was used. The existing Graphify graph guided the admission/registry
review and was checked against source. These auxiliary modules add no
production call path, and the broad tree remains dirty; refresh is deferred
without implying any new Work capability.

## Observational process cleanup recovery — 2026-09-27

**PASS for this bounded cleanup increment; FAIL for T097 acceptance. T097 and
T019 remain `[ ]`; `WORK_BACKENDS={}`.** `WorkProcessSupervisor.original_pair_terminated`
accepts only the durable v3 identity and proves termination using boot ID, PID,
starttime, and process state. When called without an explicit supervisor,
`recover_process_cleanup` observes both v3 process identities and Bubblewrap
attempts; it marks cleanup complete only after proving that both original
processes ended. Live processes, errors, and uncertain states remain
`failed`/`reconcile`, with the scheduler held. This observational path does not
reattach, call `pidfd_open`, signal, or redeliver. With an explicit supervisor,
the existing active cleanup path remains in place, including identity
revalidation and pidfd handling.

Sol's RED confirmed that omitting v3 left the attempt failed (one failure).
The first review found an important risk for Bubblewrap without a supervisor;
RED also detected `pidfd_open`. Both were addressed. Sol's final independent
review marked the finding **ADDRESSED** with no new findings. Root's fresh
verification ran four modules (`test_work_process_cleanup_recovery.py`,
`test_work_bubblewrap_cleanup_recovery.py`,
`test_work_process_supervisor.py`, and
`test_work_process_restart_contract.py`): **75 passed in 40.81 s**. Black
`--check` on four paths reported unchanged. `project-composition-check caos`
passed: **283 files, 1,033 dependencies, four kept, zero broken**; the targeted
whitespace search had no matches.

This slice does not complete T097: Bubblewrap remains unregistered and rejects
Work contracts. `WORK_BACKENDS` remains empty and T097/T019 remain `[ ]`. No
providers or operator database were used; no commit was made. The whole-tree
`git diff --check` exited 2 because of pre-existing/unrelated CRLF reports;
those files were not normalized.

## pidfd poll error handling — 2026-09-27

**PASS for this bounded helper increment; T097 and T019 remain `[ ]`.** The
`_wait_pidfd_readable` helper in `work_bubblewrap_composition.py` previously
treated any `poll` event as process exit. It now handles `POLLERR` and
`POLLNVAL` as errors first and fails closed, including when an error bit is
combined with `POLLIN` or `POLLHUP`; these events cannot falsely certify cleanup.
Parametric RED produced **two failures**; focused GREEN produced **five passes**;
the full module passed **34 tests in 27.02 s**. Sol's independent review passed
with no Critical, Important, or Minor findings. Root reran the module (**34
passed**), Black `--check` on two paths reported unchanged, composition passed
(**283 files, 1,033 dependencies, four kept, zero broken**), and the targeted
whitespace search had no matches.

This evidence covers only the helper and scratch composition. It does not
integrate `launch_staged_static_elf` into `WorkAdmission` or enable the backend.
Bubblewrap remains unregistered, `WORK_BACKENDS` remains empty, and T097/T019
remain open. No providers, operator database, or commit were used.

## pidfd tri-state and supervised orphan recovery — 2026-09-27

**PASS for this bounded increment; T097 and T019 remain `[ ]`.** The pidfd
helper now has three outcomes: `POLLERR`/`POLLNVAL` return `None`; only a clean
timeout (`False`) authorizes the pre-signal step; post-signal completion
requires `True`. With an explicit supervisor, recovery can terminate the exact
surviving namespace init after monitor loss or PID-reuse proof, but only after
repeating boot ID/PID/starttime/namespaces checks and validating the pidfd target.
Without a supervisor recovery remains observational. Bubblewrap
`--die-with-parent` has a startup PDEATHSIG race; the updated dual-outcome
integration also gates on ownership of the test-owned PIDFD.

Evidence: caller RED, **six expected failures**; orphan RED, **one failure**;
focused GREEN, **18 passed** (including six negative cases); updated full module,
**33 passed**. Root's fresh four-module suite: **92 passed in 46.08 s**. Black
`--check` on six files reported unchanged; composition passed (**283 files,
1,033 dependencies, four kept, zero broken**); targeted whitespace search had
no matches. Independent Sol review: **PASS**.

`WORK_BACKENDS={}`; Bubblewrap remains unregistered and fail-closed. T097 and
T019 remain `[ ]`.

## start snapshots and strict supervisor reattachment — 2026-09-27

**PASS for this bounded supervisor increment; T097 and T019 remain `[ ]`.**
During `start()`, structural snapshots captured with
`strict_monitor_argv=False` are compared before accepting identity; exact argv
is then validated. `reattach()` retains strict validation against the
persisted identity. Missing identity never authorizes signaling or confirming
process exit. The pidfd result remains tri-state. Abort handling avoids
inversion of `_lock` and `_wait_lock` by using an `RLock` and nonblocking
acquisition; if another waiter prevents the check, the attempt remains
`UNCERTAIN`.

Fresh evidence: **104 focused tests passed**; Black `--check` on two files
reported unchanged; composition passed (**283 files, 1,033 dependencies, four
contracts kept, zero broken**); Sol review **PASS**. `WORK_BACKENDS` remains
empty; T097 and T019 remain open.

## executable content snapshot revalidation — 2026-09-27

**PASS for this bounded increment; T097 and T019 remain open.**
`WorkExecutableContent` now reruns `WorkContracts._revalidate_order` inside
`read_snapshot`, before selecting V2, the token, or the catalog. Store reads and
staging remain after the snapshot closes. RED established that revoked, expired,
replaced, and terminal cases all reached the read before this change. Root's
fresh focused gate covering `executable_content`, `contract_binding`, and
`executable_staging` passed **80 tests**. Black `--check` on two files reported
unchanged; composition passed (**283 files, 1,033 dependencies, four kept,
zero broken**); peer review by Sol **PASS**.

This does not integrate the helper into `WorkAdmission` or the launcher and
does not grant authority to the release. `WORK_BACKENDS={}`; T097/T019 remain
open.

## Bubblewrap bound-data ELF identity validation — 2026-09-27

**PASS for this bounded slice; T097 and T019 remain open.**
`bubblewrap_bind_data_arguments` now validates that mutable identity matches
the ELF in the sealed memfd before constructing argv. It checks the **1..8 MiB**
size bound before reading. `_read_exact` uses `pread`, preserving the file
cursor. RED showed both identity mismatch and a sparse memfd larger than 8 MiB
previously failed with **DID NOT RAISE**. Root's suite covering args, staging,
Bubblewrap composition, bound content, executable content, and contract binding
passed **131 tests**. Black `--check` on two files reported unchanged; composition
passed (**283 files, 1,033 dependencies, four kept, zero broken**); Sol peer
review passed for security.

Diagnostic detail retained: `pread` `OSError` propagates with its original
`errno`, failing closed. This does not grant launch authority or integrate the
helper into `WorkAdmission`. `WORK_BACKENDS={}`; T097/T019 remain open.

## cleanup ownership advisory lock — 2026-09-27

**PASS for this bounded slice; T097 and T019 remain open, with no GO or
registration.** A per-attempt/generation advisory lock is shared by
`recover_process_cleanup` and `cleanup_attempt`. Contention is rejected before
identity access, and cleanup waits do not hold a SQLite transaction. An ordinary
process crash leaves the attempt pending so another process can resume it;
missing `fcntl` fails closed. The reservation remains held. A fork-only child
can inherit and retain the lock, delaying recovery; this is intentional
fail-closed behavior.

Root verification: **75 focused tests passed**; Black `--check` on three files
reported unchanged; composition passed (**283 files, 1,033 dependencies, four
kept, zero broken**); independent review **PASS**. `WORK_BACKENDS={}`; no backend
GO or registration.

## Pre-GO setup intent persistence, Work store v30→v31 — 2026-09-27

**PASS for this bounded persistence slice; T097/T019 remain open.** The v31
additive schema stores an immutable pending intent with a composite FK to the
exact Bubblewrap process identity. `record_pre_go` checks live order authority,
exact `sent` revision, V2 token/digest/catalog and a closed ACK of at most
8,192 bytes in one `BEGIN IMMEDIATE` transaction. Exact replay is idempotent;
a failed intent insert rolls back the identity. `read_historical` verifies
stored evidence after authority loss without granting release. The previous
public process-identity API remains compatible. Independent code review found
no critical or important blocker.

Root's fresh gate: **110 tests passed**; Black `--check` on **5 files**,
localized whitespace check clean, and `project-composition-check caos`
**PASS** (285 files, 1,037 dependencies, four kept, zero broken). Existing
Graphify queries were checked against source. Refresh was attempted but did
not complete: corpus reported 248 code/20 docs with no semantic API;
`--code-only` finished AST, then stalled resolving 9p paths and was
interrupted (exit 130). `graph.json` and `.graphify_ast.json` did not change;
refresh is deferred, not a passed gate. There is no GO/release/backend
integration or registration; `WORK_BACKENDS={}`.

## Composition review de C07 — 2026-09-28

**Veredicto: PASS WITH RISKS para las pruebas C07; T097 no se acepta como
completo.** El cambio cruza contrato/admission/launch, backend Bubblewrap,
proxy MCP por intento, persistencia durable, identidad host del broker y suite
Linux integrada. Se contrastaron los límites de cada componente con el código y
con los escenarios ejecutados: sólo se despachan herramientas autorizadas por
el contrato; cada intento recibe un proxy/socket/credencial nuevos; el intento
hermano no lee ni duplica el FD; y un efecto upstream incierto no se repite tras
reabrir el store y recrear repositorio/gateway.

Verificación: `test/integration/t097 -m t097_host` en Ubuntu 26.10/QEMU TCG,
Bubblewrap 0.13.0 allowlisted, Landlock ABI 11, Yama `ptrace_scope=1` y cuenta
`caos-work-broker`: **8 passed, 0 skipped en 335,30 s**. Para ambos casos de
proxy se fijó timeout de worker 45 s por lentitud TCG; el predeterminado sigue
en 10 s. Las pruebas de regresión locales de los contratos/dispatch sumaron
136 passed antes del último helper de fixture; luego los dos casos de proxy
pasaron 2/2 y el gate QEMU completo pasó 8/8.

Un hallazgo real precisó la frontera host: `uid_map` no separa un worker de un
proceso host ordinario con el mismo UID, que pudo usar `ptrace`/`pidfd_getfd`
fuera de los namespaces. La aceptación exige por ello cuenta broker OS
dedicada, no root, `nologin` y sin procesos host ajenos. El entorno de
despliegue aún no está disponible para esta aceptación: falta repetir allí con
timeout por defecto y digest aprobado. `WORK_BACKENDS={}`; no hay GO,
registro, ni signoff T097/T019. El resultado es parcial y no habilita uso en
despliegue.

Durante el desarrollo, compartir checkout hacía que la reserva de workspace
serializara los launches y no demostraba concurrencia; los casos ahora usan
checkouts distintos. El primer run TCG con timeout predeterminado agotó los 10 s
en una validación del proof, no en el endpoint; ambos casos proxy fijan 45 s
sólo para la emulación lenta. El run final con ese presupuesto pasó; no se
relaja el default de despliegue.

## Composition review — enforcement del broker y workflow T097 — 2026-09-28

**Veredicto: PASS WITH RISKS para la frontera de identidad y la suite QEMU;
T097/C08 siguen abiertos hasta el runner dedicado.**

- **Subsistemas cambiados:** configuración del backend Bubblewrap,
  `preflight_work`, fixture de aceptación host, workflow GitHub Actions,
  especificación/plan/tareas e informes C03/C07/C08.
- **Vecinos revisados:** `WorkAdmission._preflight` entrega la restricción al
  backend antes de admitir; Bubblewrap preflight ocurre antes de Landlock y
  cualquier probe del binario; la fixture de host valida el eUID y procesa
  exactamente `CAO_WORK_BROKER_ACCOUNT`. El broker no se envía al worker.
- **Invariantes:** cuenta configurada local; eUID y UID de cuenta no root e
  iguales; shell resolviendo a `nologin`/`false`; rechazo anterior a probes
  para identidad ausente o no confiable. El workflow corre sólo código de main,
  lee repo, requiere cuenta broker separada y no relaja timeout/digest.
- **Arquitectura:** `project-composition-check caos` PASS, 289 archivos,
  1.059 dependencias, 4 contratos kept y 0 broken. Graphify encontró
  `WorkAdmission._preflight`; se contrastó con fuente. El graph de la worktree
  no está disponible y el refresh histórico quedó atascado en 9p; no se usa el
  grafo como evidencia de comportamiento.
- **Escenarios:** identidad unitaria **11 passed**; regresión focal
  **71 passed**; suite integrada en Ubuntu/QEMU TCG con Bubblewrap 0.13.0,
  Landlock ABI 11, SQLite y workers reales: **8 passed, 0 skipped en
  327,65 s** bajo el broker y con override TCG 45 s. YAML del workflow parsea.
  Actionlint no está instalado.
- **Fallo y causa:** la selección local amplia tuvo **420 passed, 8
  deselected, 1 failed**. El único fallo es una expectativa previa de
  `test_tmux_backend.py` que no incluye `plain_shell=False`, mientras que
  el `TmuxBackend` modificado previamente sí lo pasa. No se tocó ese cambio
  ajeno.
- **Riesgo residual:** no existe runner configurado con label
  `cao-t097-host`; el workflow está creado pero no ejecutado allí. La variable
  Actions, cuenta del host, permisos sudo y repetición sin override de 10 s
  siguen pendientes. No se registra Bubblewrap: `WORK_BACKENDS={}`; T097/T019
  permanecen abiertos.
