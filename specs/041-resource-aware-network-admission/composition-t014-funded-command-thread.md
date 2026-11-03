# Composition Review Report — T014.R.H2.2b.1

**PASS WITH RISKS, componente controlado.** El traspaso de una reserva exacta registra el cargo RAM/CPU antes de crear el hilo físico y conserva su propiedad hasta observar retorno y join. Es el prerrequisito del actor, no el actor ni su financiación productiva.

## Fronteras e invariantes

Reserva pendiente → despacho registrado bajo el mutex del ledger → Thread.start fuera del mutex → entrada del hilo exacto con nueva comprobación de PID/generación/frescura/capacidad → resultado → retorno observado → join físico → retiro atómico del cargo. El conteo started incluye el despacho; no afirma que el callback ya entró. No se añaden sesiones SQL ni autoridad shared. El callback y reloj son código interno, no entradas HTTP/MCP ni una API de ejecución arbitraria para clientes.

Stop, presión, invalidación y reconfiguración solo retiran trabajo aún pendiente; no devuelven el coste de un hilo despachado. El finish ordinario del token no puede liberar ese cargo. Dos señales separan final de la llamada start de retorno del target. Un Future terminado no demuestra salida física, ni un error de start demuestra ausencia. El error conserva handle y coste; si nunca hay entrada/salida, no existe reintento ni refund soportado. Una eventual salida del mismo hilo permite su join y retiro; no se prueba con un segundo lanzamiento manual.

El resultado sync/async conserva el error original del callback. La espera async usa shield sobre un Future privado sin executor por solicitante; cancelarla no cancela el trabajo ni su resultado. Join comprueba identidad y salida fuera del mutex y rechaza self-join, otro proceso y timeouts inválidos. Es síncrono: el próximo actor deberá evitar bloquear con él el loop ASGI. No se publica un Future mutable ni se traslada FundedControlSession a otro worker.

## Evidencia ejecutada

- **1671 PASS**, cero fallos/errores/omisiones, 16 advertencias; 121 módulos. Conserva los 1.654 IDs aceptados y añade exactamente 17; selección enfocada 46 PASS. No sumar controles repetidos ni el RED.
- Una fuente/dos Python idénticos al archivo ejecutado; hash de archivo comprobado y soporte/descriptor cerrados y preservados. Formato/imports/compile aprobados; mypy en frío de 458 archivos sin errores, sin cambiar configuración ni añadir supresiones.
- Hilos nativos con barreras: cargo antes de start, entrada después de stop/generación/presión/frescura inválidas, target terminado con cola física aún viva, error antes/después de inicio, cancelación de espera, self-join/proceso ajeno/copias y 25 hilos con costes declarados sintéticos. No son 25 modelos ni aceptación de capacidad física medida.
- La regresión heredada conserva sus recorridos root/Bridge/PostgreSQL reales y contratos de diagnóstico. Mismo contenedor PG18.6/perfil; después del cierre, 0 clientes/0 bases/0 roles de prueba. Los casos nuevos no hacen SQL; no se atribuye a ellos la integración PostgreSQL heredada.
- Cobertura seleccionada de la fuente modificada: 93.14%; paquete 36.64%. No acredita cobertura global del desarrollo. Arquitectura: 461 archivos/2091 dependencias, cinco contratos conservados/cero rotos. Sin migración ni cambio de schemas, límites, deadlines o cobertura.
- ECC Arquitectura/Python en read con Luna Max y Claude read job 7e60a39d934242e7af914141d824cb94 completado/aprobado sin defectos de ciclo de vida; resultados contrastados por el lead, sin atribuirles ejecución de tests. Graphify privado parcial verificado contra fuente; 12 archivos ajenos preservados. Contratos de ciclo de vida y compatibilidad local ejecutados; no cambia el wire HTTP/MCP ni un consumidor independiente que requiera Pact nuevo.

## Riesgos y trabajo posterior

Faltan propietario persistente original, buzón tipado con coste retenido mientras haya payload en cola, emisor real del arranque, wiring CLI/runtime, activación sobre préstamo vivo y cierre/custodia. El token de arranque proviene del llamante ya financiado; este componente no resuelve esa autoridad creando un ledger/muestra propia. M1/M2/M3/SDK/UI y los diez gates originales permanecen abiertos.

[Validación](validation-t014-funded-command-thread.json).
