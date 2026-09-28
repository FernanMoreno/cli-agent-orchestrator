# S05 — Registro durable de efectos MCP e incertidumbre

Causa: [C05](../causas/C05-emision-durable-sin-efectos-inciertos.md)

## Implementado

La migración v34 agrega el journal append-only de efectos:

- `work_mcp_proxy_effects` conserva identidad del intento/generación, digest
  SHA-256 del JSON-RPC normalizado y hora. La unicidad por intento, generación
  y digest impide repetir una solicitud idéntica.
- `work_mcp_proxy_effect_events` registra `intent`, `completed`,
  `failed_before_effect`, `uncertain` y `reconciled`, con transiciones
  monotónicas. Sólo guarda digest de respuesta y de la nota de conciliación.

El proxy registra `intent` y cierra la transacción antes de llamar al upstream.
Revalida permiso, lease, issue y proof antes del efecto. Una llamada iniciada
que no alcanza terminal durable queda incierta; el replay continúa bloqueado.

La migración v35 agrega `work_mcp_proxy_issue_events` para distinguir issues
`issued`, `abandoned` y `reconciled`; backfillea los issues de v34 como
`issued`. Permite detectar un endpoint consumido que murió antes de registrar
su primera petición. La conciliación de effects e issues conserva sólo el
digest de la nota explícita.

En el arranque de la API, antes de cargar proveedores, y durante limpieza
posterior, `WorkService` toma el lock del dueño y comprueba la identidad
durable exacta del par de procesos. Sólo después de confirmar que el proceso
terminó cambia intents a `uncertain` e issues sin efectos a `abandoned`. Una
identidad ausente, viva o imposible de verificar queda pendiente; nunca se
adivina la muerte ni se reemite el endpoint.

La API exige `cao:admin` para consultar y conciliar:

- `GET /work-proxy/effects` lista sólo identidad, generación, estado, tiempos
  y digest del request; no devuelve contenido del request ni de la respuesta.
- `POST /work-proxy/effects/{effect_id}/reconcile` cierra un efecto sólo si
  está `uncertain`; conserva el digest de la nota explícita.
- `GET /work-proxy/issues` muestra issues pendientes sin efectos.
- `POST /work-proxy/issues/{attempt_id}/{generation}/reconcile` cierra un
  issue sólo si el proceso terminó y el issue quedó `abandoned`.

Los journals son append-only y no expiran: conservar sus filas conserva la
restricción de no replay. La retención automática queda desactivada hasta que
exista un diseño que preserve esa cerca de idempotencia.

## Verificación

Las pruebas cubren las dos ventanas de SIGKILL —antes de entrar al callback
upstream y después de simular el efecto externo—, el rechazo de redelivery,
la recuperación sólo tras muerte probada, y la lectura/conciliación admin de
effects e issues. Otra prueba mata el proceso entre el DDL de v35 y su commit;
al reabrir, v34 queda íntegro, verifica y migra a v35. La compatibilidad v34
backfillea los issues existentes sin cambiar sus datos.

Esto cierra C05. No cierra la aceptación T097 completa: los gates host-nativos
y la suite integrada siguen en C01/C03/C04/C07/C08.
