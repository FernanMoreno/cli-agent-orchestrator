# T014.R.H2.4b — composición Root opt-in

Root CLI publica el binding antes de SQL y recibe activación/refresh/stop por un socket privado separado. El padre construye el subject desde el binding y kernel autenticados. El despliegue verifica administrador y conexión financiada antes del arranque; el operador provisiona explícitamente recursos para el hijo vivo antes de activar. La conexión original del emisor se conserva. El wrapper retiene el proceso y el registro poscommit y prohíbe reemitir.

## Fronteras e invariantes

- Reserva positiva RAM/CPU antes de Popen; mismo PID/hilo/tarea y controles no reentrantes. El gate incluye emisor, key_supplier y entrega. Refresh/stop previos a ready se rechazan sin cerrar el canal.
- Binding v2/ACK sin cambios; Root usa otra pareja AF_UNIX/SEQPACKET, credenciales PID/UID por mensaje, codec estricto con secuencia y bounds, rechazo/cierre de derechos recibidos, timeout por operación y espera idle mientras viva el padre. El descriptor y el selector no conceden autoridad.
- No SQL antes del paquete firmado. El emisor usa una sola vez el canal original; SQL5/resource7 se conservan. La provisión del fixture ocurre en un worker administrativo con su propio permiso de almacenamiento y cargo de cómputo, sin transferir la conexión original.
- Fallo RPC consume/cierra solo el protocolo. Join posterior puede retirar únicamente cómputo del padre tras binding/observación de salida/cierre de todos los handles positivos. ACK no es retiro; cleanup conserva el cargo. Créditos SQL e intención permanecen.
- Opt-in falso conserva la llamada anterior a la factory y el protocolo stdio. RootSqlSubject conserva la clase exacta mediante reexport. No se añaden hilos lectores, colas sin bounds ni un máximo agregado fijo de agentes.

## Superficie revisada

CommandReservation/CommandProcess, supervisor/kernel/sockets, modelo puro y codec, Root CLI, emisor y certificado existentes, ResourceBootstrap, conexión financiada, repositorio administrativo, actor/monitor, cuotas y créditos. Revisiones ECC de arquitectura, seguridad, arnés Python e inventario; Claude read independiente de producto y corrección del arnés. Las revisiones son estáticas; el lead ejecutó las pruebas.

## Verificación

**626 PASS**, 10 advertencias, 39 módulos, cero fallos/errores/omisiones: conserva572 IDs previos y añade54. Los50 controles unitarios/IPC local y4 casos del nuevo arnés incluyen3 recorridos Root Linux/PostgreSQL reales. El caso sano bloquea stdin/stdout, verifica cero TCP del hijo antes de activación, uno después, cinco reentradas poscommit denegadas, refresh/stop/join y deuda SQL conservada. Firma rechazada y EOF validan sus límites. Los casos de la selección y el control nativo previo se solapan; no se suman.

Los14 Python/8 fuentes coinciden con el archivo ejecutado. Tipos467 en frío, Black/isort14, arquitectura5 contratos/0 rotos; sin cambios de configuración ni supresiones nuevas. Mismo PostgreSQL18.6 y keeper, limpieza0/0/0 tras cierre; doce archivos Graphify ajenos preservados. Grafo privado parcial actualizado y contrastado con fuente. [Recibo](validation-t014-root-control-deployment.json).

## Diagnósticos y límites

La revisión final reprodujo además tres devoluciones indebidas tras fallo de cierre del descriptor Root. Se conserva una marca física monotónica en supervisor y wrapper; ni un segundo cierre positivo autoriza devolver el cargo. Un fallo RPC puro con cierre confirmado sigue siendo joinable. Las tres regresiones añadidas y la selección626 son la evidencia final; el candidato623 se conserva superado.

El primer diagnóstico real produjo2 fallos/2 pases: el arnés intentó otra conexión dentro del préstamo ocupado del emisor. La denegación era correcta. Se conserva ese recibo fallido; se separó la provisión en un worker financiado y se leyó el agregado sobre la conexión original, sin ampliar límites ni rebajar aserciones. El recorrido corregido y la regresión final son la evidencia aceptada.

PASS en el componente interno opt-in. El cómputo padre es sintético y la provisión administrativa es de pruebas. argv/env pertenecen a código confiable; el binding no acredita ejecutable. Instalación productiva de autoridad, API central, shared, custodia, M1/M2/M3, modelos y SDK/UI restante siguen separados. Permanecen abiertos T013, T014, T015, T016, T018, T019, T020, T021, T022, T023.
