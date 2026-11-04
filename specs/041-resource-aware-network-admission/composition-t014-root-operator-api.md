# T014.R.H2.4c — operador Root y API opt-in

El operador autenticado puede iniciar un Root independiente, consultar el subject real, activar tras la provisión externa y pedir refresh/stop. La composición confiable inyecta un ledger y permiso de almacenamiento dedicados, firmante/trust, destino y comando fijos. La API no concede presupuesto ni acepta claves, argv o entorno por HTTP. No convierte su PID legacy en Root.

## Fronteras revisadas

- JWT admin validado en cada petición, identidad estable fijada al iniciador y rechazo previo a muestrear/reservar/SQL. Estado descriptivo sin SQL; comandos y respuestas Pydantic estrictos y errores fijos/no-store.
- Una reserva financia el worker antes de abrir su única conexión original. PID/hilo/tarea, canal, emisor y controles físicos permanecen en ese worker; permit disjunto del monitor API. No se transfieren capacidades ni se generan claves o grants.
- Un comando normal en vuelo y reserva antes de publicarlo. Cancelación HTTP conserva operación, payload y Future protegidos. Retiro del cargo y publicación de respuesta son atómicos para snapshots/admisión bajo el mismo RLock. Finish no positivo retiene cargo y comando.
- El cierre usa el worker ya financiado, bloquea controles nuevos y espera los activos. El registro monotónico de intento de activación decide el presupuesto: si nunca se intentó, join no bloqueante antes de EOF/cleanup; activación intentada o desconocida conserva toda la espera antes de cleanup. Estado cached o ACK perdido no prueban inactividad.
- Join precede siempre a cleanup. Cleanup positivo puede probar salida física y permitir cierre de SQL sin autorizar refund. Se conservan runtime/handles/registro/cargos ante incertidumbre; metadatos que fallen después no revocan una prueba física positiva. El retiro exige cierre SQL nativo, préstamo y lease originales retirados y salida efectiva del hilo.
- Lifespan retira Root antes de la API. Cancelación se difiere hasta terminar la tarea de cierre; fallo Root no omite la limpieza independiente de la API ni sustituye un fallo previo. Sin runtime configurado, la API autenticada devuelve503.

## Verificación

**748 PASS**, 10 advertencias en 53 módulos; cero fallos/errores/omisiones. Conserva los626 IDs previamente aceptados, añade58 casos Root y comprueba64 casos existentes adicionales del lifespan/API. Las cifras de las ejecuciones enfocadas se solapan y no se suman.

La selección incluye cuatro recorridos Root CLI/PostgreSQL reales: tres existentes y uno nuevo por ASGI/JWT/provisión externa/activación/refresh/stop/cierre. Una precomprobación verifica permisos SQL reales de provisión. Presupuesto padre y autoridad de pruebas declarados: no acredita IdP externo, financiación productiva ni concurrencia de modelos.

Doce Python/cuatro fuentes son idénticos al archivo ejecutado. Tipos470 fuentes en frío sin configuración relajada ni supresiones nuevas; Black/isort12, arquitectura5 contratos/0 rotos. PostgreSQL18.6 reutilizado con identidad/keeper preservados y0clientes/0bases/0roles tras cierre. SQL5/resource7 intactos; doce Graphify ajenos preservados. Grafo privado parcial contrastado y actualizado. [Recibo](validation-t014-root-operator-api.json).

## Fallos corregidos y límites

Los diagnósticos fallidos se conservan. Un getter tras cleanup positivo perdía la prueba física; el cierre usaba una fase desactualizada; la publicación de respuesta quedaba después del refund. Cada causa se reprodujo antes de corregirla. La ampliación encontró además dos puertos falsos sin el scope SQL requerido y un plugin de prueba que esperaba para siempre pese al shield ya existente: los fixtures ahora comprueban scope/reconciliación y cancelación diferida con liberación explícita, conservando sus aserciones. Los intentos interrumpidos no son aceptación.

PASS para el adaptador opt-in. Quedan instalación productiva del issuer/signer/trust y su financiación disjunta, arranque de la API bajo autoridad real cuando corresponda, shared/custodia, M1/M2/M3, modelos e integración SDK/UI restante. No se añade un límite agregado fijo de agentes o entregas. Los diez gates originales siguen abiertos: T013, T014, T015, T016, T018, T019, T020, T021, T022, T023.
