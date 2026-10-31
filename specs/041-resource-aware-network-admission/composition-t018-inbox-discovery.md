# T018 — reservas de almacenamiento y descubrimiento Inbox

Resultado: PASS del componente local. T018, M1/M2/M3 y los gates globales siguen abiertos.

StorageReservation usa el mismo ledger, observación, presupuesto, ceiling, guard y retiro físico de las entregas. Su huella solo consume almacenamiento y no crea un receptor ficticio. La disponibilidad de RAM/CPU para una entrega no sustituye la disponibilidad de almacenamiento; ausencia de autoridad, datos caducados y presupuesto agotado siguen denegando. Los totales de operaciones incluyen el scanner para impedir un retiro prematuro del runtime. Los contadores separados de entregas y almacenamiento son incrementales y no representan agentes inventados.

Una copia de lease podía incrementar el contador de retiros pendientes antes de que el retiro por identidad la rechazara. Dos RED reproducen el fallo, con y sin préstamo activo. La comprobación del registro exacto precede ahora a cualquier mutación y se comparte con el nuevo tipo.

Las tres entradas Inbox reservan dos sesiones por entrega antes de leer o marcar filas. El scanner reserva su sesión antes de avanzar el iterador y mantiene su scope en el hilo que realmente posee SessionLocal/cursor. El cierre ocurre dentro del scope; el crédito se retira antes de esperar hijos y se notifica la capacidad liberada. Se conserva el primer error y se unen todos los hijos conocidos. Un cierre incierto conserva su préstamo y la operación hasta una prueba física de cierre.

La composición real con PostgreSQL mantiene un cursor y dos envíos con advisory y ORM: cinco préstamos. Después de cerrar y retirar el cursor quedan cuatro mientras ambos envíos siguen activos. Los dos casos de una unidad disponible comprueban cero construcciones de SessionLocal y cero checkouts del descubrimiento, dejando la fila PENDING. La denegación del consumidor conserva ordinal, contexto y registry. Las fixtures limitan el mismo número lógico de entregas por RAM y declaran las sesiones aparte; el staging genérico conserva su unidad.

Verificación: 176 PASS de primitivas, 138 PASS de integración inicial y ampliación final de 181 PASS, cero errores y omisiones. Los conjuntos se solapan; no se suman como pruebas únicas. Se conservan 17 RED (API pendiente y dos fallos reales de identidad) y siete RED de las fronteras Inbox. Un descriptor preparado, nunca ejecutado, se sustituyó antes de correr al alinear la fixture de reapertura con las cantidades declaradas. No se redujeron criterios, límites, timeout ni cobertura configurada.

Formato de siete archivos y mypy de dos módulos PASS, con la cobertura habitual de código sin anotaciones. Import Linter/composición: 439 archivos, 1.951 dependencias, cinco contratos KEPT y cero BROKEN. No cambian esquema, ACL, API externa, permisos de proveedores ni el máximo del pool auditado. El contrato está dentro del proceso; las pruebas de consumidor y PostgreSQL cubren sus fronteras, sin necesidad de Pact. Revisión estática independiente Codex y revisión de composición/diff del lead PASS en este alcance. Graphify anterior se contrastó con la fuente y se conserva sin regenerar sobre cambios ajenos.

El mismo PostgreSQL termina con cero clientes de prueba/ajenos a la sonda, cero bases y cero roles de prueba; contenedor y coordinador permanecen disponibles. El conocimiento durable se guarda mediante transacción local del vault. Los escenarios puros de 1, 5 y 20 entregas prueban cantidades, no capacidad de veinte modelos reales.

Quedan control SQL protegido y otras entradas, retry de cierres propios en shutdown, separación del dominio auditado en el monitor, diagnóstico autorizado y aceptación física independiente. M2/M3, SDK/UI y gates de proveedores/globales se mantienen separados. [Recibo](validation-t018-inbox-discovery.json).
