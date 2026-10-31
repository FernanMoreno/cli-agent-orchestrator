# Composición T018: retiro de almacenamiento por PID

PASS en el componente de cierres conocidos. T018 y M1/M2/M3 siguen abiertos.

- Subsistemas: Connection propio/prestado, registry físico, sampler y lifecycle compartido Inbox/Bridge. Vecinos revisados: pool in-use, scopes/loans, monitor/control futures, API/Bridge shutdown shield y fork de handles registrados.
- Ownership explícito: loan=None no distingue dueño de fachada. Borrow close no cierra raw; el dueño retiene un cierre conocido fallido con o sin loan. El registro conserva callbacks de cierre ya solicitado de todos los targets del proceso; no concede permisos por perfil ni abre reemplazos.
- Retiro: monitor/control/participant drain preceden al pass físico unido. Registry closed=True antecede a liberar tokens. Sampler solo atestigua; monitor thread retirado no equivale a conexión cerrada. Unknown-open sin handle permanece bloqueado.
- Cancelación: worker de cierre unido ante cancelación repetida; persistente mantiene PID/binding/registro, una siguiente llamada puede reintentar el mismo handle. No se añaden timeouts ni liberación por tiempo.
- Fork: el nuevo sampler hijo solo existe tras detach/registry confirmado. Referencias padres fuertes se conservan sin locks ni retiro; hook en PID propio o fallo no borra estado. Factory rechaza sampler extranjero. No acepta fork durante connect ni handles externos no registrados.
- Pruebas: RED9fallos (6PGreales/3APInueva), núcleo75PASS; fork1FAIL/1PASS conservado y corregido; final418PASS,0errores/0omisiones/19warnings. Las ejecuciones se solapan. PID real observado antes/después de cierre; PostgreSQL/JWKS/HTTP/Bridge, clientes, pools, conocimiento y esquema/ACL/filas mantienen contratos.
- Arquitectura:440archivos/1958dependencias/5KEPT/0BROKEN; formato9/tipos5PASS. MismoPG y dueño vivo,0clientes adicionales/0bases temporales/0roles; infraestructura preservada.
- No nuevo wire/schema/migración; contratos internos y listener existentes probados directamente, no nuevo consumidor externo que requiera Pact. Graphify sin nodos recientes, contraste directo con fuente; índice ajeno preservado.
- Límites: crédito protegido y entradas SQL restantes, presupuestos físicos combinados y aceptaciónM1, M2/M3/SDK/UI/gates globales pendientes.

[Recibos y bytes probados](validation-t018-storage-retirement.json).
