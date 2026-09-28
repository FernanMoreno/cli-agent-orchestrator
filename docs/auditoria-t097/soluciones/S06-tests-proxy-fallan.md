# S06 — Reparar y actualizar las pruebas del proxy

Causa: [C06](../causas/C06-tests-proxy-fallan.md)

## Resultado

Los tests históricos que esperaban un socket con nombre o un proof no ligado
se sustituyeron por escenarios del contrato actual:

- emisión durable one-shot y ausencia de secreto antes de ACK/proof;
- rechazo de bindings incorrectos, proceso terminado y snapshot alterado;
- transporte por FD e identidad del socket observada en ACK;
- intención durable antes del upstream, sin transacción abierta durante la
  red, resultado incierto ante excepción, conciliación explícita y replay
  idéntico bloqueado;
- revocación despierta lector bloqueado y espera una solicitud en curso.

La ejecución focal de `test/services/test_work_mcp_proxy.py` y
`test/security/test_work_bubblewrap_isolation_proof.py` pasó **15 tests**. La
última matriz combinada (proxy, proof, composición Bubblewrap, snapshots,
seccomp/Landlock, migraciones, registro y sibling probe) pasó **219 tests con
3 omitidos** por capacidades del host.

Los skips corresponden a capacidades del host, no a xfail que oculte fallos.
El registro `WORK_BACKENDS` sigue vacío mientras C07/C08 estén abiertos.
