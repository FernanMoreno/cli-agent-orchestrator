# T014.R.H2.2b.2 — Propietario original de control

Veredicto: **PASS WITH RISKS**, componente opt-in; no aceptación global.

El actor conserva un único hilo nativo no daemon que abre y posee el canal raíz y FundedControlSession. El llamante aporta una reserva positiva exacta de RAM/CPU del mismo ledger y el certificado firmado existente. El actor no inventa capacidad, créditos ni autoridad de despliegue.

## Fronteras e invariantes

Se revisaron delivery_admission, root_sql_control, FundedControlSession, ResourceBootstrap, AdmissionMonitor, PostgresPressureSampler, diagnósticos y sus consumidores heredados. Refresh es tipado: cobra y enlaza el mensaje al worker registrado antes de publicarlo; revalida generación/frescura/capacidad antes de SQL. No hay submit(callback), executor por petición ni techo fijo de mensajes/agentes. Diagnóstico v1/v2 combina ledger actual y telemetría SQL cacheada sin abrir canales.

El primer ciclo acredita readiness; la recuperación de capacidad conserva los tres muestreos saludables de la política. Los ciclos periódicos usan la reserva de infraestructura retenida, incluso cuando todavía no se admiten comandos. La cancelación separa al solicitante; no libera trabajo aceptado. La sesión incierta termina sin SQL posterior, replay ni reconnect.

Stop rechaza su propia cola antes de drenar participantes externos; excluye únicamente su préstamo de control original. El resultado y la retirada exitosa del cargo se publican bajo el mismo guard del ledger, sin callbacks de aplicación ni SQL allí. Un finish fallido conserva el error y el cargo. Solo cierre nativo positivo, retirada del préstamo y join del hilo exacto retiran el arranque. Cierre repetido/concurrente y ausencia probada de worker son distintos de un arranque incierto.

## Evidencia ejecutada

- **320 PASS**, cero fallos/errores/omisiones; 10 advertencias, 20 módulos. Conserva exactamente 286 IDs relevantes del baseline de 1.671 y añade 34: 26 unitarios y 8 de composición nativa. No sumar las 72 comprobaciones unitarias solapadas ni los 5 pases del primer diagnóstico fallido.
- PostgreSQL real: 28 solicitudes refresh de salud (3 secuenciales + 25 concurrentes), un worker/canal/backend; cancelación bajo barrera con cargos retenidos; poison sin SQL posterior; tres rechazos sin conexión y ACK de arranque perdido con retiro positivo. Costes y observaciones compute sintéticos; no son 25 modelos ni capacidad M1 medida.
- Saturación en el proceso nativo con canal PostgreSQL vivo: una reserva externa consume las cantidades RAM/CPU declaradas restantes; el refresh adicional devuelve resource_exhausted sin llegar al worker protegido. Su contador permanece en 28 y hay un solo driver. El cargo externo termina en la misma tarea antes del cierre. No se introduce un máximo fijo ni se atribuye esta contabilidad sintética a presión física del host o de PostgreSQL.
- Cuatro Python/dos fuentes idénticos al archivo ejecutado y controlador archivado exacto; los tres documentos de pendientes también coinciden con el archivo de ejecución; soporte/fuentes/descriptor preservados. Formato/imports aprobados, mypy de 459 archivos en frío sin errores ni supresiones nuevas. Cobertura del paquete seleccionado: 23.69%, no cobertura global. En estas mismas pruebas, delivery_admission alcanza el 75,26% y original_control_owner el 87,97%; no se compara esta selección con la cobertura de una regresión más amplia.
- Misma identidad PostgreSQL 18.6/perfil/keeper; tras cierre, 0 clientes, 0 bases y 0 roles de prueba. SQL schema 5 y resource schema 7 sin cambios. Cinco contratos de arquitectura conservados/cero rotos. Los contratos locales y recorridos HTTP/Bridge heredados están incluidos; no cambia ningún wire HTTP/MCP ni consumidor desplegado que requiera un nuevo Pact.
- Revisiones ECC Luna Max de arquitectura/base de datos/Python y Claude read; hallazgos reproducidos y corregidos. Graphify privado parcial contrastado contra fuente; 12 archivos ajenos preservados. Obsidian conserva únicamente conclusiones durables mediante transacción inspeccionada/lint0.

## Fallos y límites

El primer diagnóstico dio 3 fallos/5 pases: el arnés asumía capacidad inmediata tras ready; la recuperación de política explica una posible negativa, pero no se capturó la primera razón y no se declara demostrada retrospectivamente. Un assert dejaba vivo el worker no daemon y provocaba un timeout secundario. Una segunda selección dio 319 pases/1 fallo: el finally cerraba otra vez un actor ya retirado y reemitía el error esperado de poison; ahora solo inicia ese cierre si falta prueba de retiro. Ambos recibos se conservan como fallidos. El arnés espera la señal publicada del ciclo real, cierra en finally y verifica cwd/origen/binding explícitos; no baja política, límites, deadlines ni cobertura. Los RED separados acreditan reloj fallido antes de lanzar, cleanup doble y la ventana retirada/respuesta.

El emisor de financiación de arranque, wiring CLI/runtime, activación shared sobre préstamo vivo y custodia/reconciliación siguen pendientes. Los holds conservadores por ausencia de evidencia física no constituyen un protocolo de custodia. M1/M2/M3/SDK/UI y los diez gates originales siguen abiertos. No se ha repetido aquí todo el baseline de 1.671: la selección de 320 casos cubre el componente y sus vecinos afectados.

[Validación](validation-t014-original-control-owner.json).
