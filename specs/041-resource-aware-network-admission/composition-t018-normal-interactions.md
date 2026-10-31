# Composición T018: operaciones SQL normales y ciclo de vida

PASS del componente de interacciones después del bind. T018 completo y M1/M2/M3 permanecen abiertos.

- Subsistemas: dueño físico, puerto normal reutilizable, InteractionService, API/MCP/AG-UI, arranque/cierre y auditoría de autorización. Vecinos: repositorios Work/Interaction, préstamos nativos/ORM/auditoría, errores HTTP, principal/capability, epochs y registro de cierres.
- Reservas: cada contexto normal tiene una sesión, sin receptor ni costes de entrega CPU/RAM. Esquema frío y conexiones secuenciales reutilizan la lease. Un scope normal exacto del mismo dueño no se cobra otra vez; control, otro dueño, copia y epoch viejo fallan cerrados, también en legacy. Los gates marcan denegación sticky.
- Ciclo: la lifetime conserva identidad y actividad mientras se espera; el crédito SQL se libera entre sondeos/yields. Stop no elimina compensación ya dueña; solo una lease normal ya iniciada puede reutilizar su crédito, nunca reservar otro. Restart exige actividad cero y cambia epoch.
- Cierre: el runtime conserva solo participantes enlazados; une todos los drenajes aunque uno falle/cancele. Retry físico, testigo y owner.inflight0 preceden a retirarlo. Binding fallido o incierto no vuelve a legacy. Una referencia retirada no puede cerrar el nuevo binding compartido.
- SQL/errores: helper raw común conserva AgentActor, une workers y deja JSON al mapper. Saturación en auth de capacidades devuelve503estructurado y cero SQL. AG-UI usa worker unido fuera del loop y conserva fallback sanitizado/revalidación de scopes. Las esperas async aún hacen SQL síncrono breve por poll: ownership seguro no acredita NFR global.
- Target: registro limitado al main configurado, con identidad/store_id. No supone dominio físico compartido por URI ni adopta auth scope. Inicialización previa al bind sigue legacy; su worker ya no queda abandonado al cancelar.
- Pruebas: final539PASS/0FAIL/0ERROR/0SKIP/18warnings,888.42s. Se conservan los RED y checkpoints solapados75/78/59/461/463/71; no se suman como casos distintos ni se presenta un checkpoint anterior como verificación del código final. SQL real, HTTP/MCP, role/ACL/CAS, FIFO/Work, SSE y reinicio conservan sus aserciones.
- Autorización: auditoría global reconoce únicamente4handlers reales de admisión con dependencia resuelta y guard ejecutado JWT/admin/sealedPrincipal. Negativos eliminan JWT/admin, cambian dependencia/verificador y prueban wrappers/firmas sustituidas. No se añaden rutas públicas. El doble de tareas ejecuta el callback finito real mediante CodeType exacto y mantiene los actores de fondo falsos.
- Arquitectura:441archivos/1967dependencias/5KEPT/0BROKEN; formato17/tipos8PASS (notas de cuerpos main no tipados conservadas). Mismo PostgreSQL y dueño vivo,0clientes extra/0bases temporales/0roles, contenedor intacto.
- Contratos: API existente y OpenAPI/HTTP/MCP reales; helper interno y puerto de lifecycle, sin migración SQL ni consumidor externo nuevo que requiera Pact. Graphify existente sin anchors recientes; se contrastan fuentes/callers y arquitectura, preservando los12artefactos ajenos.
- Límites: arranque presupuestado, otras entradas/auth-domains, polls completamente off-loop, presupuestos físicos combinados y M1/M2/M3/SDK/UI/global pendientes.

[Recibos y bytes probados](validation-t018-normal-interactions.json).
