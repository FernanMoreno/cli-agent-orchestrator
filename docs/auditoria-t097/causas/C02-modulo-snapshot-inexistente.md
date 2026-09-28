# C02 — Falta el módulo de snapshot del runtime y su integración en el lanzamiento

**Severidad:** crítica (la mitigación de C01 no existe)
**Estado:** confirmado en la revisión base; corregido en el corte actual

## Síntoma

- `test/security/test_work_bubblewrap_runtime_snapshot.py` no se puede ni
  recolectar:
  `ModuleNotFoundError: No module named 'cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot'`.
- `work_mcp_proxy.py:180-188` importa de ese mismo módulo la clase
  `WorkBubblewrapRuntimeIsolationProof`; el import falla y el proxy lo
  convierte en `WorkMcpProxyUnavailable("verified launch isolation is unavailable")`.

## Causa exacta

El trabajo se hizo en orden test-first y se detuvo en RED:

1. Se escribió el test de contrato (`prepare_runtime_snapshot`,
   `RuntimeSnapshotError`, `snapshot.runtime_path/package_path/path`,
   `validate()`, `close(cleanup_confirmed=...)`), pero
   `src/cli_agent_orchestrator/services/work_bubblewrap_runtime_snapshot.py`
   **no existe** en el árbol.
2. Se añadió a `work_mcp_proxy.py` un consumidor
   (`WorkBubblewrapRuntimeIsolationProof.require_current()`) de un tipo que
   tampoco existe.
3. `_build_bwrap_argv` no se modificó: sigue montando los árboles vivos del
   host (ver C01). Aunque el módulo existiera, **no hay caller** que lo use en
   `_launch_owned_stage`.
4. Tampoco hay test compuesto que ejecute Bubblewrap con el snapshot y cree un
   socket tardío en el origen para demostrar que la carrera se cierra.

## Alcance incompleto del diseño del test

Incluso el contrato del test sólo cubre `runtime`, `source` y `deps`.
`/usr`, `/lib` y `/lib64` siguen siendo binds vivos del host en
`_build_bwrap_argv`; un socket tardío en `/usr/...` o `/lib/...` sigue siendo
alcanzable aunque el snapshot se implemente tal como lo describe el test.

## Relación

- Causa raíz directa de [C01](C01-socket-tardio-accesible.md) y de
  [C06](C06-tests-proxy-fallan.md).
- Solución: [S02](../soluciones/S02-modulo-snapshot-inexistente.md).

## Estado actual

El módulo ya existe y `work_bubblewrap_composition.py` lo prepara, valida
antes de `Popen`, monta sus tres árboles privados y conserva/elimina el
snapshot según el resultado de reap. El caso real con Bubblewrap y las pruebas
de mutación/cleanup pasan. Esto cierra la ausencia del módulo, pero no los
límites de binds base ni de aceptación del host descritos en S01/S08.
