# C3.A — revisión de composición

**PASS en el componente; broker y activación global pendientes.**

- Subsistemas revisados: journal, modelos descriptivos, migrador/política central de ACL, financiación original, supervisor/sujeto kernel y consumidores C1/C2. Ningún adaptador SQL o proveedor cambia de permiso.
- Orden: actor/canal financiados → sujeto vivo → posición bajo lock → ancla/identidad/secuencia → INSERT RETURNING verificado → commit → comprobación final. La lectura no autoriza ejecución ni replay.
- Persistencia: SQL3 aditiva, FK compuesta, índices para secuencia y ordinal; hashes SQL1/2 y resource1–7 conservados. SQL2 debe migrarse por el dueño antes de runtime. Las cuotas lógicas no son financiación de sesiones ni bytes.
- Fallos reproducidos: política append-only incompleta y recibo sin fila. Se corrigieron el migrador y la confirmación de inserción sin relajar verificadores. Los dos controles de commit y la lectura del recibo confirmado se conservaron.
- Concurrencia/ownership: mismo PID/Thread/task/canal financiado. Las solicitudes de un journal se serializan bajo su lock; no se transfieren writers. Los tests anteriores de escritores competidores y cuotas permanecen. No hay hilo por agente ni techo fijo de agentes nuevo.
- Contratos: nuevo recibo y métodos Python, sin API HTTP/MCP/SDK ni protocolo externo nuevo; Pact no aplica. Sujeto autenticado real y PostgreSQL real verifican la composición; no se simula autoridad de transporte.
- Pruebas: 1.360 PASS, sin fallos, errores ni omisiones en 98 archivos; 1.329 casos previos y 31 nuevos. Cuatro fuentes y seis archivos Python coinciden con el código ejecutado. La suite enfocada anterior aprobó 69 casos. Se prueban 1/5/20 planes secuenciales, observación independiente antes de ACK, replay cruzado, cuota/fence, sujeto ajeno/caducado, ausencia de inserción, commit incierto y pérdida postcommit, migración SQL2→3 con ancla real y permisos efectivos.
- Arquitectura: 457 archivos/2058 dependencias; 5 contratos conservados y ninguno roto. Formato de 6 archivos y tipos de 4 fuentes aprobados. Revisión Claude read en 3 rondas y revisión raíz de tests/diff; Graphify privado parcial contrastado con fuente. Obsidian: 3 páginas y cero incidencias de lint.
- Recursos: mismo contenedor/imagen PostgreSQL18.6 recuperado tras reinicio de WSL, entorno desechable y coordinador de reutilización verificados; al terminar, cero clientes, bases o roles de prueba pendientes. No se tocaron credenciales del operador/Claude ni jobs históricos.

## Límites

El sujeto solo sigue vivo dentro de before_ack. Este corte registra solicitudes; no paga sesiones, emite permisos posteriores ni implementa broker/renovación/Bridge. Variar path/usuario/TLS/scope es dato descriptivo dentro del binding, nunca autorización por operación. Un caso de 20 planes no prueba 20 agentes concurrentes. Root, inventario completo, custodia, M1/M2/M3, SDK/UI y los diez gates continúan abiertos. La selección de regresión no establece cobertura global.

[Validación](validation-t014-child-followup-intents.json).
