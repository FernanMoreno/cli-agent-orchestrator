# C06 — La suite del proxy tiene 2 fallos

**Severidad:** media (síntoma; la causa real es C02/C03)
**Estado:** fallos originales reproducidos y corregidos; suites focales pasan

## Reproducción

```
.venv/bin/python -m pytest -q test/services/test_work_mcp_proxy.py -p no:cacheprovider
FAILED test_bound_proxy_claim_precedes_secret_and_cannot_be_reissued
FAILED test_bound_proxy_rechecks_revocation_before_upstream_effect
2 failed, 5 passed
```

Error decisivo en ambos:
`ModuleNotFoundError: No module named 'cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot'`
→ `WorkMcpProxyUnavailable: verified launch isolation is unavailable`.

## Causa exacta

- Ambas pruebas esperan que `create_bound_attempt` **emita un endpoint** con
  socket con nombre (la ruta incompleta de C03).
- El código productivo añadió después la exigencia de una
  `WorkBubblewrapRuntimeIsolationProof` cuyo módulo no existe (C02); el gate
  se cierra antes del issue y las aserciones del test no se alcanzan.
- Es decir: el código es **más** fail-closed que los tests. Los tests
  describen un comportamiento que no debe aceptarse hasta cerrar C02–C05.
  Hacer pasar esos tests sin resolver esas causas sería regresión de
  seguridad.

## Relación

- Solución: [S06](../soluciones/S06-tests-proxy-fallan.md).

## Estado actual

Los tests dejaron de depender de endpoints por ruta y proofs arbitrarios; el
proxy ahora verifica proof ligado, socketpair y journal durable. La suite de
proxy/proof y los casos compuestos pasan en la evidencia listada en S06. No se
relajó el gate de registro.
