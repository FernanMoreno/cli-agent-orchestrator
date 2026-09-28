# C05 — La emisión durable no registra efectos inciertos ni permite conciliarlos tras reinicio

**Severidad:** alta (riesgo de efecto duplicado o perdido tras crash)
**Estado:** mitigado y verificado en el corte actual

## Causa exacta

1. **Hallazgo original: sólo se registraba la emisión.** La tabla
   `work_mcp_proxy_issues` (`clients/work_mcp_proxy_schema.py`) guarda
   `attempt_id, generation, attempt_revision, contract_hash, expires_at,
   issued_at` y es inmutable (triggers `immutable_update/delete`). No tiene
   estado (`issued/serving/effect_started/completed/uncertain/reconciled`),
   ni id de request JSON-RPC, ni digest del request, ni resultado.
2. **Hallazgo original: la incertidumbre vivía sólo en memoria.** En `serve_once`
   (`work_mcp_proxy.py:341-400`) `attempted = True` es una variable local; si
   el proceso muere después de `self._upstream(...)` no queda rastro durable
   de que el efecto upstream pudo ocurrir. `WorkMcpProxyUncertain` es sólo una
   excepción al caller.
3. **Hallazgo original: efecto externo dentro de la transacción SQLite.** `self._upstream(...)` y
   `client.sendall(...)` se ejecutan dentro de
   `with self._repository.transaction()`. Si el upstream tiene éxito y luego
   falla el commit (o el proceso cae), la transacción hace rollback, pero el
   efecto externo ya ocurrió: no hay "intent" comprometido antes del efecto.
   Además mantiene el lock de escritura de la DB durante una llamada de red.
4. **Hallazgo original: setup parcial sin conciliación.** Si el issue se inserta y luego falla la
   creación del socket (`:266-271`) sólo se lanza
   `"proxy issue was consumed but endpoint setup failed; reconcile before retry"`;
   no existe ninguna función de conciliación ni lectura en arranque que
   detecte issues huérfanos (`serve`/`revoke` son no-ops, `:403-412`).
5. **Hallazgo original: sin reattach.** Tras reinicio no se puede reconstruir el endpoint (bien,
   por diseño) pero tampoco marcar el issue como `abandoned`/`uncertain` para
   bloquear redelivery y avisar al operador.

## Relación

- Solución: [S05](../soluciones/S05-registro-durable-efectos-inciertos.md).

## Estado actual

S05 ahora conserva eventos de efecto e issue. Al arrancar, el supervisor marca
como incierto un efecto o issue sólo si la identidad durable confirma la
terminación del proceso exacto; el replay sigue bloqueado y la consulta o
conciliación exige `cao:admin`. Una identidad viva, ausente o no verificable
permanece pendiente.

Las pruebas con `SIGKILL` cubren el efecto antes y después del callback
upstream, y la muerte durante DDL verifica rollback íntegro y compatibilidad
v34→v35. Ver [S05](../soluciones/S05-registro-durable-efectos-inciertos.md)
para rutas admin, política de retención y evidencia. El gate global T097 sigue
abierto por causas independientes C01, C03, C04, C07 y C08.
