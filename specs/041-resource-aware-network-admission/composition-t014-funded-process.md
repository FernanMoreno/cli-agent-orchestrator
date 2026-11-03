# T014.R.H2.4a — Vida de proceso financiada

Veredicto: **PASS WITH RISKS**, primitiva interna opt-in; no despliegue Root ni aceptación global.

CommandProcess enlaza una reserva positiva RAM/CPU del ledger padre con un ResourceProcessSupervisor exacto. Registra el cargo antes de intentar Popen y hace el I/O fuera del guard. Rechaza bases root, créditos SQL, handoff a hilo y finish/cancel prematuros. No añade otro ledger ni un número fijo de procesos, agentes o entregas.

## Fronteras revisadas

La propiedad física se conserva en el mismo PID, objeto Thread y Task. El handle nativo queda privado: publicar un supervisor mutable permitiría pasarlo a otra tarea del mismo hilo y eludir esa comprobación. receive_binding comprueba afinidad/estado antes del socket y guarda binding/etiqueta autenticados. join exige la misma ProcessExitObservation sellada del supervisor, binding/etiqueta exactos y handles cerrados antes de retirar bajo el guard. No hay callback, SQL o espera nativa bajo ese guard.

Un timeout permite volver a esperar al mismo hijo. La reentrada se rechaza mediante una puerta no bloqueante; una prueba inválida o un fallo de espera/cierre vuelve terminal la incertidumbre. Cleanup y señales siguen ligados al propietario exacto; cleanup puede cerrar al hijo, pero conserva el cargo y bloquea cualquier refund posterior. join/cleanup sin timeout explícito hacen un poll; un timeout finito del llamante permite esperar. Stop, presión, invalidación y reconfiguración conservan los iniciados.

Se revisaron ledger/reservas, CommandThread, actor original, supervisor/kernel, binding/exit, modelos de prueba, bootstrap y sus vecinos de diagnóstico/API/Bridge. No cambia HTTP/MCP, el protocolo de binding ni SQL5/resource7. No hay consumidor desplegado nuevo que requiera Pact: se prueban directamente contratos internos, consumidores heredados y procesos/PG reales.

## Evidencia

**572 PASS**, cero fallos/errores/omisiones, 10 advertencias, 33 módulos; 349.393 s de recibo. Conserva 542 IDs relevantes de la unión aceptada de 1.843, y añade 30: 27 unitarios y 3 controles del arnés nativo (API presente y dos recorridos con hijos Linux reales). La selección conserva todos los 30 módulos del corte anterior. Los 89 unittest locales y los tres controles nativos iniciales se solapan; no se suman.

El hijo sano conserva el cargo durante poll/stop y retira solo tras señal, wait auténtico y close. El binding inválido conserva el cargo incluso después de cleanup positivo y otro intento de join. El propio hijo verifica cwd/origen de paquete; se reutiliza el publisher real y se observa el cargo antes de delegar al Popen real. La capacidad declarada del padre es sintética; estos procesos no representan modelos ni un benchmark de capacidad física.

Los tres Python/una fuente coinciden con el archivo ejecutado de cuatro miembros, incluido su controlador; fuentes/soporte/descriptor preservados. Black/isort pasan y mypy frío de las 463 fuentes finales pasa sin supresiones ni cambios de configuración. Cobertura seleccionada: 25.29%, sin atribuirla al paquete global. Arquitectura: cinco contratos conservados/cero rotos. PostgreSQL 18.6 del mismo contenedor/perfil/keeper, 10813 MiB disponibles al admitir frente a mínimo 6.144; tras cierre cero clientes/bases/roles de prueba. Doce Graphify ajenos preservados; grafo privado parcial contrastado contra fuente, revisión ECC/Claude read y transacción Obsidian de tres páginas con lint cero.

## Correcciones y límites

Los RED conservados reprodujeron retiro tras incertidumbre, consumo del socket antes de validar, reentrada, transferencia del supervisor mutable entre tareas, default de join bloqueante y pérdida del handle local si se interrumpía su adjunción. La última corrección conserva el supervisor ya retornado y el error primario. Los primeros tipos/pruebas del candidato no se presentan como evidencia de las modificaciones posteriores; el recibo final usa los bytes actuales.

Una interrupción dentro de Popen o antes de recibir/asignar su resultado puede dejar un hijo sin handle gestionable y cargo retenido. Si falla pidfd, no se permite señalar por PID sin pin: solo esperar/cerrar pasivamente. Un owner de contexto ajeno se conserva pero no se controla. No se debilita la prueba kernel ni se inventa no-arranque, cierre de descendientes, SQL o custodia. Los métodos son síncronos; una espera finita bloquea a su propietario.

Quedan H24b (publisher de identidad Root CLI y transporte acotado del paquete), el emisor/supervisor productivo y la API central. Shared, custodia/reconciliación, M1/M2/M3, integración SDK/UI restante y diez gates originales siguen abiertos. El CLI Root todavía no llama esta primitiva ni publica el binding de supervisor; esta composición no lo declara desplegado.

[Validación](validation-t014-funded-process.json).
