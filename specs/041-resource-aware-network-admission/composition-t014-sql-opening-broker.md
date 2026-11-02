# C3.B — revisión de composición

**PASS en el componente; activación productiva y custodia pendientes.**

- Subsistemas: modelos v4, supervisor/kernel/IPC, gate físico común, financiación C1, journal y migrador/ACL. Vecinos revisados: bootstrap/Bridge, tres adaptadores, recuperación y ledger compartido. No se activa Bridge ni se cambian perfiles MCP.
- Orden: startup autenticada y cargo original → solicitud identificada → sujeto efímero vigente → nueva unidad prepagada → plan y asociación atómicos con RETURNING → commit → revalidación → respuesta → guard del worker → driver. Emisión, recepción y apertura física siguen siendo hechos distintos.
- Propiedad y locks: canal exacto/PID/Thread/task/scope/raw/target originales comprobados antes de leer. Mensajes serializados fuera de state.guard; exchange liberado antes del driver. Sin hilo nuevo por agente ni techo global fijo. Cierre y fork no heredan autoridad.
- Persistencia: SQL4 aditiva conserva hashes de SQL1–3 y resource1–7. Mapeo unívoco de cargo hacia startup o followup, CHECK/FK y unicidad nativos; política central/verificador append-only coherentes. Startup v3 histórica no se remapea. El dueño es confiable y puede migrar; no se promete un trigger que bloquee al dueño.
- Fallos reproducidos: 4 RED de ciclo de vida y 1 RED de task ajeno. Corregidos Idle/EOF, rechazo local sin poison, incertidumbre solo para solicitudes enviadas, monotonicidad de instalación/cierre y ownership antes de IPC. Un claim sin fila tras rollback es deuda retenida posible, no permiso de reintento ni corrupción automática.
- Pruebas reales: tres adaptadores, 20 aperturas posteriores y agotamiento, 5 workers SQL en un hijo, observador independiente ve cargo/mapeo antes de conceder. La barrera reforzada está dentro del driver; el backend permanece idéntico a ambos lados de otra barrera. Fallos antes de INSERT, commit incierto, pérdida postcommit, cargo reutilizado, destino/recibo erróneos y mapeo ausente conservan deuda y uso de la sesión inicial. SQL nativo cubre CHECK/FK/UNIQUE/ACL; una fila descriptiva extra es fixture owner-only, no concesión.
- Regresión final: 1405 PASS / 0 fallos, errores u omisiones, 17 advertencias, en 101 archivos; los 1.360 casos anteriores y 45 nuevos. 180 enfocados anteriores y repetición reforzada de un caso existente; no se suman casos duplicados. 8 fuentes y 11 archivos Python coinciden con el archivo ejecutado; formato de 11 archivos y tipos de 8 aprobados.
- Arquitectura: 457 archivos, 2059 dependencias, 5 contratos conservados y 0 rotos. Claude en modo read, 3 rondas, raíz revisó fuente y tests finales; Graphify privado parcial contrastado con fuente. Obsidian: 3 páginas, 0 incidencias de lint.
- Contratos: API Python/socket interno, sin nuevo protocolo HTTP/MCP/SDK desplegado por separado; Pact externo no aplica. Dependencia PostgreSQL 18.6 real, mismo contenedor/coordinador de reutilización verificado; final: 0 clientes, 0 bases y 0 roles de pruebas pendientes. Sin cambios a login/credenciales del operador o jobs históricos.

## Límites

Prueba de 5 workers SQL no es aceptación de 5 agentes, modelos nativos o red distribuida. Se necesita integración con un worker/scheduler original: el IPC es síncrono con deadline; no se transfiere Connection a otro executor. Padre inactivo devuelve Idle; cliente agotado después de enviar termina, pudiendo dejar deuda. Poll de HUP es best-effort, no prueba ausencia ni elimina todas las carreras. Un descendiente previo al attach podría leer un FD heredado y causar DoS, sin suplantar credenciales; después del attach, fork lo cierra.

Código Python/SQL arbitrario del runtime confiable queda fuera de esta frontera. Las cuotas son lógicas y no acreditan bytes/sesiones de toda la jerarquía. Root, primer TCP de control, inventario completo, custodia independiente, Bridge, M1/M2/M3, SDK/UI y los diez gates siguen pendientes. Esta regresión no demuestra cobertura global del producto.

[Validación](validation-t014-sql-opening-broker.json).
