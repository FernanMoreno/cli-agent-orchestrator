# Composition Review Report — T014.R.H2.1 / H2.2a

**PASS WITH RISKS, componentes controlados.** El monitor puede reutilizar el worker/canal original y el ledger puede reservar RAM/CPU para comandos sin otra sesión SQL. No implementa todavía el actor ni su arranque productivo.

## Composición e invariantes

Envelope/bootstrap/monitor conservan target fresco, binding completo, rol/ACL/esquema y transacciones funded externas readonly. El permiso se comprueba contra su scope/lease original sin anidar operation; afinidad antes de propiedades con locks. El modo ligado rechaza async start y writers privilegiados. Rollback observado permite recuperación; perder esa observación o la posterior al commit conserva la sesión bloqueada y propaga la clase correcta. Un ticket de ciclo invalida su generación propia tras reconfigure sin borrar otra posterior.

CommandReservation cobra huella interna positiva RAM/CPU, storage0, en registro/pending/counters separados. Recencia, generación y ajuste por componentes se verifican al reservar/iniciar, sin depender de unidades de entrega ni otro techo de cantidad. Solo el worker físico PID/thread/task que inició puede finalizar. Stop/invalidate/reconfigure/observación no retiran cargos iniciados; presión mezcla pendientes por secuencia en tiempo lineal y omite costes que no reducen la deuda. No crea préstamo SQL ni crédito shared. Los totales incluyen comandos y los campos delivery/storage/control mantienen sus categorías.

El diagnóstico v2 es estricto y negocia versión explícita, con commands.inflight/started y conservación de totales. V1 sin comandos mantiene su estructura congelada y llamadas originales; si hay comandos responde409 fijo diagnostics_version_required, required_version2/supported_versions[1,2]. Query inválida responde422 tipado invalid_diagnostics_query con no-store; otras rutas conservan invalid_resource_envelope. JWTadmin y scopes permanecen.

## Evidencia ejecutada

- **1654 PASS, 0 fallos/errores/omisiones**, 16 advertencias, 120 módulos. IDs exactos: conserva los1.541 aceptados y todos los1.598 de la selección anterior; añade113 frente al último corte aceptado. Selección enfocada final: 269PASS. No sumar ejecuciones parciales.
- Ocho fuentes/doce Python coinciden con el archivo ejecutado y su hash; fuente, soporte y descriptor cerrados y preservados. Tipos en frío:458archivos sin errores; formatter/imports equivalentes al hook y compile de12 aprobados.
- Cobertura de la selección: 92.47% de líneas de las ocho fuentes modificadas; 36.57% de todo el paquete. Esto no acredita cobertura/aceptación global.
- Root firmado y PG reales: driver count1, mismo backend en ledger/autoridad/presión y25reservas/inicios/finalizaciones de comandos. Costes retenidos durante stop y préstamo SQL original intacto. Linux usa observaciones deterministas; no son25modelos ni M2físico. Principal de factory verificada, sin login real.
- Contratos ASGI/HTTP/modelos: v1congelado, v2,409,422,no-store,scopes y consumidores heredados incluidos. Son pruebas de contrato del producto, no una integración SDK/UI desplegada. No consumidor independiente nuevo que requiera Pact externo; modelos strict y pruebas wire son el contrato ejecutado.
- Arquitectura: 461 archivos/2091dependencias; cinco contratos conservados, cero rotos. Siete fuentes de esquema/financiación/activación sin cambios. PG18.6 mismo contenedor/imagen/perfil, después del cierre0clientes/0bases/0roles de prueba. Se recuperó su montaje efímero tras reiniciar el entorno; las credenciales del operador no se tocaron.
- ECC Python/DB/seguridad: LunaMax con los prompts instalados, revisión read-only y resultados contrastados por el lead; no ejecutaron los tests. Hallazgo de query reproducido y corregido. Graphify privado parcial actualizado; doce archivos ajenos intactos.

## Fallos, compatibilidad y límites

Se reprodujeron antes de corregir: constructor que tomaba lock antes de rechazar worker ajeno; DBError que ocultaba rollback no observado; invalidación contra generación anterior a reconfigure; keywordNone en fábrica antigua y keywordversion por defecto en proyección antigua; query mal etiquetada. La primera regresión completa quedó1597PASS/1FAIL y no se aceptó. La parada solicitada cerró otra ejecución con exit2 y no se presentó como verde. El workflow exige ahora buscar consumidores/test doubles y conservar la llamada por defecto antes de congelar.

Faltan actor/ejecutor/cola, financiación de su arranque RAM/CPU, wiring productivo/CLI, activación sobre préstamo vivo y sellado por actor, emisor/custodia y gatesM1/M2/M3/SDK/UI. Los diez gates globales permanecen abiertos. Ningún perfil de proveedor ni permiso MCP se amplía con este corte.

[Validación](validation-t014-root-control-runtime.json).
