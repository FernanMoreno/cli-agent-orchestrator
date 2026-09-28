# C04 — La prueba de aislamiento del proxy no está ligada a intento, generación, contrato ni proceso

**Severidad:** alta
**Estado:** cerrado en la proof actual; aceptación global sigue sujeta a C07/C08

## Síntoma

En la revisión base, `create_bound_attempt` y `serve_once` aceptaban cualquier objeto
`WorkBubblewrapRuntimeIsolationProof` cuya `require_current()` no lance.

## Causa exacta

- `work_mcp_proxy.py:180-188`: la verificación es
  `type(proof) is WorkBubblewrapRuntimeIsolationProof` +
  `proof.require_current()` **sin argumentos**. No se le pasa ni se compara:
  - `attempt_id` ni `generation` del issue;
  - `contract_hash` efectivo;
  - identidad del proceso (PID de namespace, starttime, pidfd, inode de
    PID-ns) que produjo el ACK;
  - identidad de la ejecución Bubblewrap (`WorkBubblewrapExecution`).
- `serve_once` (`:346-349`) repite la misma llamada sin binding.
- Consecuencia: una prueba válida del intento A (o de una generación anterior,
  o de un proceso ya reemplazado) autoriza el endpoint del intento B mientras
  `require_current()` sea cierto en abstracto.
- No hay registro durable que ate proof ↔ issue: la fila
  `work_mcp_proxy_issues` (`clients/work_mcp_proxy_schema.py`) no tiene
  columnas de identidad de proceso ni de proof.
- La identidad Bubblewrap persistida por `WorkBubblewrapSetupIntent.record_pre_go`
  (v31) existe, pero el proxy no la consulta.

## Relación

- Depende de [C02](C02-modulo-snapshot-inexistente.md) (el tipo ni existe).
- Solución: [S04](../soluciones/S04-prueba-aislamiento-ligada.md).

## Estado actual

S04 añade proof sellado e inmutable, lo liga a la emisión y al ACK persistido,
comprueba pidfds, starttime de ambos procesos, identidad durable, PID/user
namespace y snapshot, y lo exige antes de cada efecto. Las pruebas cambian
starttime y namespace tras emitir proof y confirman su rechazo. La resistencia
de hermano mismo UID queda verificada por S03 bajo Yama=1. C04 queda cerrada;
el gate global permanece cerrado por C07/C08.
