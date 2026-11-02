# C3.C — revisión de composición

**Integración de arranque controlada validada; ocho errores de tipos previos permanecen.**

- Cambiados: entrypoint Bridge y adaptador de supervisor. Vecinos revisados: publisher/capability, journal SQL4, scope/canal/reserva originales y teardown de proceso.
- Orden: validar configuración/UUIDs/worker/destino → Popen fijo → binding autenticado → cargo/plan confirmado → permiso inicial → init_runtime_db → constructor RemoteOperationStore → restauración → frontera de admisión. El servicio HTTP/WS propio empieza después y no resuelve el primer TCP.
- Propiedad: launch/grant/serve/join quedan en el worker original con el scope de control activo. Ningún hilo/pool/contador de agentes nuevo. El scheduler futuro debe multiplexar por readiness y servir durante shutdown financiado.
- Fallos: original y dueño se retienen incluso antes de yield; cleanup no reemplaza el error primario. RED de wait sin observación corregido: no retornar éxito ni cerrar el handle en esa salida normal incierta. Cerrar/recoger proceso no devuelve SQL ni acredita ausencia de backend.
- Compatibilidad: argv fijo y recorrido de identidad legados conservados. Marker estricto exige protocolo existente, sin excepción de permisos. Un realm distinto en env falla en handshake y otro rol/TLS/path falla en el guard previo al driver; no hay nueva migración o autoridad desde JSON.
- Dependencia real: PostgreSQL 18.6, mismo contenedor/coordinador, cierre final 0 clientes, 0 bases y 0 roles de prueba. Bridge ejecuta SQL con rol runtime y verifica componentes reales; observador independiente ve la financiación antes del permiso posterior.
- Aceptación: 1.423 PASS / 0 fallos, errores u omisiones / 16 advertencias, 103 archivos; conserva 1.405 identidades y añade 18. Los 35 enfocados finales se solapan; no se suman. 5 archivos Python (2 productivos) coinciden con el archivo ejecutado.
- Formato: 5 archivos aprobados. Mypy con las mismas opciones devuelve los mismos 8 errores de 3 dependencias importadas que HEAD, verificados mediante shadow-file; no hay nuevos diagnósticos ni supresiones y no se declara PASS de tipos global.
- Arquitectura: 457 archivos, 2062 dependencias, 5 contratos conservados / 0 rotos. Revisión GPT-6 Luna Max read, 3 rondas; raíz contrastó fuente, reprodujo y corrigió el último hallazgo. Claude no revisó: falló por cuota antes de diagnosticar. Graphify privado parcial; Obsidian, 3 páginas y lint 0.
- Contratos externos: socket/Python interno y mismo proceso de despliegue, sin nuevo servicio HTTP/evento desplegado por separado; Pact no aplica.

## Límites

La instrumentación privada detiene el Bridge en la composición de admisión y sustituye construcción de backend nativo. No ejecuta modelo, proveedor, HTTP/WS ni red distribuida. El contexto exige financiación previa y no está conectado a la raíz CLI actual. El fallback de cleanup no es custodia ni prueba éxito del sello; el llamante debe mantener servicio de broker para apagado ordenado.

Se conserva un rechazo histórico seguro de vista del proceso antes del primer driver cuya causa concreta no se capturó; las vistas estables de la regresión final no lo explican retrospectivamente. Ni el rechazo ni el cierre liberan cargos. Root, primer TCP de control, inventario, custodia independiente, los diez gates, M1/M2/M3 y SDK/UI siguen abiertos.

[Validación](validation-t014-funded-bridge-startup.json).
