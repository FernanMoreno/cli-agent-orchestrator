# T080/T081 — frontera del contrato y canal final ambiguo de v2

2026-10-23. Corrección de código en main; aceptación nativa nueva pendiente. Se conservan los FAILED de T077/T079 y el PASS v1 T078 ligado a8057.

T079 reprodujo la primera frontera incorrecta: el helper seleccionaba el último encabezado, repetido de forma incompleta en texto posterior, y devolvía vacío pese a un contrato activo completo anterior. La selección ahora exige un bloque completo con el recibo activo; compara su digest mediante el verificador existente. Se permite el wrapping solo dentro del eco del contrato. No se acepta un contrato antiguo completo en lugar del actual incompleto, ni un recibo inline/incorrecto/citado como prueba de finalización. Ambos consumidores usan la misma selección.

El replay intermedio recuperó1221caracteres de la captura real; contenían el recibo activo y dos pares ACK/recibo. Eso no acreditó respuesta pública: el cuerpo incumplía el ACK estricto. La revisión de composición detectó que producción podía asentar ese texto aunque el diagnóstico lo rechazase. T081 añade rechazo de extracción para un preámbulo Thinking sin transición de canal demostrada en turnos con recibo; no selecciona párrafos ni el último ACK. FULL conserva el transcript sin asentar el recibo. Un marcador nativo puede seguir informar COMPLETED; por sí solo no verifica la entrega.

TDD: dos regresiones iniciales RED para encabezado incompleto posterior, después casos de wrapping/contrato antiguo/inline; cuatro RED adicionales para Thinking con uno/dos recibos y con/sin duración. El fixture histórico stale-duration permanece intacto: se endurece a rechazo de canal ambiguo. Sus controles positivos de duración antigua se conservan con respuesta sintética normal de uno y varios párrafos. No se rebajan plazos, memoria, modelo, ACK ni controles del recibo.

Revisión independiente: Codex confirmó selección activa y rutas de extracción previas al settlement (incluido viewport), detectó la brecha de Thinking y aprobó el rechazo conservador del fixture histórico. Claude read no produjo revisión: agotó su cuota de sesión; no se reintentó autenticación ni se cambió login.

## Composición y límites

Cambian solo parser v2 y regresiones de proveedor/servicio. Se revisaron BaseProvider, terminal_service, historial/viewport, testigos de recibo y asentamiento durable. No cambian schema/DB, transacciones, permisos, modelo, reintentos ni recursos. El catálogo/perfil real y cao.work.message siguen siendo gates distintos.

Graphify previo identificó34 aristas del propietario; el helper reciente no figuraba y se contrastaron en fuente sus dos consumidores. No hay dependencia nueva; no se regenera sobre los12 archivos Graphify ajenos. El replay real guardado se rechaza por canal ambiguo, sin publicar texto ni tokens. Los controles de parser usan fixtures y la ruta de servicio real con almacenamiento sustituido para demostrar ausencia de settlement; no afirman una nueva ejecución de PostgreSQL o modelo.

Riesgo residual: una respuesta legítima que empiece por Thinking puede quedar sin verificar al no existir frontera estructurada fiable. Se prefiere conservarla visible y sin asentamiento a adivinar su canal. Falta la revalidación nativa en la fuente integrada; no hay aceptación final/global.

Validación final offline:218 pruebas enfocadas PASS en126,16s,2warnings, con imports fijados explícitamente a main; Black/isort y diff-check PASS. Arquitectura:412archivos,1834dependencias,5contratos conservados/0rotos. El replay privado T079 se rechaza por canal ambiguo; la prueba de servicio FULL conserva el transcript y demuestra cero llamadas de settlement. El viewport fue revisado en fuente, sin afirmar una nueva prueba específica de esa ruta. Veredicto de composición: PASS WITH RISKS para las correcciones offline; aceptación nativa y soporte de canal estructurado aún pendientes.
