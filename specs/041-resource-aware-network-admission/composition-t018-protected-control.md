# Composición T018: SQL protegido y entrada de mutación

PASS del componente monitor/control tras bind; T018 completo y M1/M2/M3 siguen abiertos.

- Subsistemas: ledger y préstamos físicos, monitor/recuperación, runtime API/Bridge, DTO diagnóstico y rutas administrativas. Vecinos: PostgreSQL nativo, pools/adapters, registro de cierres, lifecycle y autenticación JWT/CAS/ACL.
- Infraestructura: un permiso privado por identidad del mismo dueño/PID/guard registra una operación storage-only iniciada; solo un préstamo físico simultáneo. No hay receptor ni coste de entrega CPU/RAM. Se permite reutilización secuencial únicamente tras retirar el token exacto. La reserva normal no obtiene excepción de autoridad desconocida.
- Presupuesto: S incluye el crédito protegido. Datos reciben max(0,S−1) antes de reconfigure/evaluate/recovery/ceiling; la observación PostgreSQL conserva su inventario incluido el probe. No A+1 ni doble cargo a committed de datos. S=1 admite control y cero datos. No es una reserva compartida M2 ni un número fijo de agentes.
- Concurrencia: los tres SQL del monitor son secuenciales; registered toma el mismo refresh guard sin invalidar como escritor. Prueba con SQL real pausado verifica que el lector espera y no abre una segunda sesión; cada apertura ya tiene el préstamo, cada cierre lo retira.
- Incertidumbre: copia de permiso/lease/token o PID extranjero no crea/retira autoridad. Cierre fallido retiene el PID real y la misma lease; retry físico confirmado precede al nuevo control. Stop conserva los dueños existentes.
- Frontera corregida: el borrador marcaba authority_change entered antes de admitir el scope protegido. RED1FAIL/1PASS confirma que cero callback contaminaba el latch. Ahora el permiso entra antes del marcador; rechazo previo no deja incertidumbre y permite otra escritura, fallo posterior sigue latched. No se excluyen errores por su tipo a ciegas.
- Diagnóstico: campos tipados separan cantidad reservada, operación y préstamo de control. Snapshot O(1), presupuesto nulo sin base, endpoint autorizado sin SQL/probe/loader por GET. Las cantidades nunca acreditan agentes vivos ni autoridad distribuida.
- Pruebas: RED núcleo6FAIL y monitor6FAIL/6PASS; núcleo57PASS, wiring85PASS y checkpoint214PASS se conservan como evidencia solapada. Final216PASS/0FAIL/0ERROR/0SKIP/18warnings,97.85s. JWT/JWKS/HTTP y PostgreSQL reales API/Bridge mantienen CAS/revocación y cobertura parcial cerrada; las otras lecturas de autoridad son fixtures controlados, no testigos M1 físicos.
- Arquitectura:440archivos/1960dependencias/5KEPT/0BROKEN; Black/isort11 y tipos6 PASS. Mismo PostgreSQL y dueño vivo;0clientes extra/0bases temporales/0roles, contenedor preservado.
- Contratos: DTO diagnóstico local aditivo de feature aún sin SDK independiente publicado; pruebas directas HTTP/API/Bridge, sin consumidor externo nuevo que requiera Pact. No hay migración SQL. Graphify existente54428nodos sin anchors actuales; fuentes/callers y arquitectura se contrastan directamente. Se conserva el índice ajeno sin reconstruirlo.
- Límites: arranque anterior al bind, entradas SQL normales/composiciones con varios pasos, identidad de dominio por target y presupuestos físicos combinados siguen pendientes; no se declaran T018/T019/M1/M2/M3/SDK/UI/global completados.

[Recibo y bytes probados](validation-t018-protected-control.json).
