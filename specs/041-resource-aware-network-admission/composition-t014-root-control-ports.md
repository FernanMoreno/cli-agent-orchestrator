# Composition Review Report — T014.R.H1

**PASS WITH RISKS, puertos controlados.** El repositorio de admisión y el muestreador pueden reutilizar la conexión ya financiada del root, sin actor ni activación compartida sobre un préstamo vivo.

## Fronteras e invariantes

FundedControlSession conserva PID, objeto thread, task, scope, Connection/raw/target/loan/funding originales y una vida única. El check local de worker funciona dentro de su transacción; comenzar otra exige IDLE/no-BUSY. Posee una transacción funded externa por operación; no adopta handles, abre TCP, reconecta, cierra el handle ni libera cargos. Un cuerpo que falla permite reutilización solo tras rollback/IDLE/financiación verificados. Commit o comprobación posterior inciertos envenenan el wrapper y conservan la clase de incertidumbre, sin denegación ordinaria ni grant.

El repositorio fija sesión/binding en construcción y comprueba la vista completa antes/después de consultas. Todos sus runtime readers/auditoría/contadores/deuda y transacciones usan ese canal sin fallback; conserva ACL/schema/uso. Los writers owner se rechazan antes de su auditoría/SQL. Los caminos legados sin sesión conservan connect/read-only/savepoint/cierre. El muestreador resuelve el target fresco, verifica roles y presupuesto LOCAL en una transacción read-only, y nunca registra el canal ajeno como probe propio. Si el rollback no se verifica, no publica un sample de recuperación.

Connection, _ControlSqlFunding, esquemas, checksums y authorizer shared permanecen sin cambios. El scope ya validaba task: la primera hipótesis contraria del peer fue contrastada y corregida. Operaciones del puerto no son autoridad de activación; el test conserva la negativa ante el préstamo vivo.

## Ejecución y revisión

- **1541 PASS, 0 fallos, 0 errores, 0 omisiones**, 16 advertencias, 111 módulos. Conserva los 1.528 IDs anteriores y añade 13 distintos; no se suman selecciones parciales.
- Tres fuentes/cinco Python idénticos al archivo ejecutado; fuente, soporte y descriptor de ejecución preservados. Tipos en frío: 458 archivos de src sin errores; formato real equivalente al hook y compile de cinco archivos aprobados.
- Root subprocess y PostgreSQL reales: driver count1 y backend estable entre auditoría, créditos/cobertura, counters, deuda, reserva/transición y muestras sucesivas. Pruebas readonly, timeout, rollback, error antes del body, task/thread/fork/scope/cierre/reentrada, ACK perdido y comprobación postcommit perdida. Las deudas metadata/operación persisten al cerrar.
- Arquitectura: 461 archivos, 2085 dependencias; cinco contratos conservados y cero rotos. PostgreSQL existente verificado y al finalizar sin clientes, bases ni roles de prueba pendientes.
- Graphify privado parcial actualizado; doce archivos ajenos intactos. Claude read completó tres rondas sin ejecutar tests: su hallazgo postcommit se reprodujo en dos tests antes de corregir. Luna Max implementó el draft del wrapper/tests en worktree privado; lead revisó, corrigió y ejecutó desde main.
- Wire/certificado y PostgreSQL internos del mismo producto; sin nuevo servicio desplegado por separado. Pact no aplica a este componente. No se ejecutan proveedores ni login JWT real: principal de la factory interna verificada de pruebas.

## Fallos y límites

El fixture inicialmente pasó un ledger donde el scope pedía una reserva de control real; se corrigió solo el test. Mypy detectó una anotación incompatible de __reduce_ex__, corregida a SupportsIndex sin casts/supresiones. El fallo funcional postcommit podía devolver StorageAdmissionUnavailable después de un commit exitoso; ahora devuelve FundedSqlCommitUncertain y el repositorio conserva SharedCommitUncertain y deuda.

Faltan actor/cola/admisión de comandos, EnvelopeRepository/Bootstrap persistentes, wiring de monitor/CLI, emisor de despliegue y custodia. No recrear un wrapper para eludir incertidumbre; el actor debe conservar el suyo durante su vida. M1/M2/M3, SDK/UI y los diez gates globales permanecen abiertos. Un puerto operativo no amplía permisos ni fija el número de agentes.

[Validación](validation-t014-root-control-ports.json).
