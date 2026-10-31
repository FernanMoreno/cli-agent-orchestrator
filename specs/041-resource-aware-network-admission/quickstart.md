# Validation guide — 041

1. Leer spec/plan/tasks y el manifiesto del intento. Verificar main, estado git, memoria y dueño/identidad exacta del PostgreSQL existente. No iniciar contenedores alternativos ni modelos.
2. Ejecutar los controles puros de política y reservas con COVERAGE_FILE y temporales privados. Esperado: adaptación por CPU/memoria/almacenamiento, desconocido/caducado denegados y reservas iniciadas retenidas tras cancelación.
3. Tras integrarM1, ejecutar la selección InboxService/API/contexto/orden/barridos/drenaje y sampler. La profundidad de cola y4workers de una fixture no se presentan como límite de producto.
4. Con código/runtime congelados y memoria>=6144MiB, ejecutar la aceptación real deM1 y conservar fuente, valores de política, actividad y cierre. Los deadlines originales no cambian.
5. M2 exige competencia real entre dos coordinadores sobre PostgreSQL, reservas atómicas, límites combinados y fallos inciertos. M3 exige incorporación real de nodos y encargo ordinario completado; no basta broker con Deployment creado.
6. Ejecutar `project-composition-check "$(cat .ai/project-name)"`, revisión, diff y verificación antes de integrar. Mantener pendientes los hitos sin ejecución; conservar la diferencia entre controles offline y aceptación real.

## Control del Bridge implementado en T010

En el despliegue del Bridge, CAO_ADMISSION_POLICY_FILE activa el mismo controlador que usa el API. Debe ser ruta absoluta a un archivo regular privado del operador, sin enlaces; contiene política, no credenciales ni claims de jerarquía. Sin la variable permanece compatibilidad; una configuración indicada inválida no habilita fallback de INPUT. Se necesita autenticación JWT ya configurada, esquema ResourceEnvelope aprovisionado y owner/writer separado explícito. init_runtime_db verifica dependencias y no crea autoridad.

Antes de conectar el canal, Bridge abre el control exclusivamente en127.0.0.1 con puerto efímero y anuncia su URL al operador en el log de arranque. La propiedad AdmissionControlServer.url permite consumo directo desde composición; los programas no deben extraer un contrato del texto del log. El ready file conserva solo PID+newline. GET/POST /admission/resource-envelope y POST /admission/resource-envelope/revoke requieren JWT admin cao:admin; el token de canal no sirve. Los DTOs y CAS son los mismos del API. Registro con cobertura incompleta mantiene capacidad cero; cambiar permisos del cliente no sustituye autoridad del servidor.

El cierre cierra admisión, espera lector/handlers/INPUT y sockets, retira actores de fondo y después HTTP/control/monitor. Esto no ejecuta aceptación M1: aún corresponden T011 y el intento físico T019 con fuente/política/mediciones congeladas. Véanse validation-t010-control.json y composition-t010-control.md.

Integración de código T001–T011 validada: los controles Inbox ahora comparan presupuestos variables sin convertir valores de fixtures en límites productivos. El siguiente gate M1 es T019: manifiesto nuevo con fuente/runtime/política congelados, calibración por invocación serial, contraste independiente de recursos/ámbitos y cierre físico. Los173controles T011 no sustituyen ese intento ni autorizan claims de jerarquía sin evidencia.
