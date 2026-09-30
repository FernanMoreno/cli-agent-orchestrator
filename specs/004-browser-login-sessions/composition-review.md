# Revisión de composición — 004

2026-09-30. Superficie revisada: navegador → auth/config/session → cookie/origen → resolución Principal → permisos API/Work → SQLite browser; ciclo logout/reset/restore → canales y estado de pestañas.

- Identidad: conserva la tupla sellada issuer/subject/kind=jwt/scopes; username no elige owner ni autoridad. Authorization explícito inválido no recurre a cookie; precedencia igual en AG-UI y WS.
- Estado compartido: SQLite browser separado de Work, esquema completo e integridad/FK verificados al arrancar; alta/epoch/version/revocación transaccionales. Restauración invalida en staging antes de publicar; rutas client.env apuntan al destino definitivo.
- Concurrencia: cookie opaca estable, renew/logout sin Set-Cookie; high-water del reloj persistido, límites idle/absolute no se amplían renovando. Generaciones frontend descartan respuestas anteriores; avisos entre pestañas son indicios, no autoridad.
- Efectos/reintentos: sólo GET/HEAD tienen un reintento controlado; escrituras inciertas no se repiten. Logout retira acceso y canales, sin cancelar agentes ni modificar recibos durables.
- Canales: origen exacto y permisos comunes; monitor cada segundo, validación antes de emisión/input y por chunk de PTY. Silencio no evita revocación. Outage de almacenamiento se diferencia de revocación y no convierte un resultado ya admitido en una escritura repetida.
- Recursos: scrypt versionado con capacidad acotada, locks privados de tooling, tareas de monitor canceladas al terminar transporte; la revisión final incorpora liberación de reservas de verificación fallida.
- Compatibilidad: opt-in, bearer tradicional conservado; imports de auth sin dependencia de clients/database/tmux. Cinco contratos de Import Linter y gate de composición pasan.

Infraestructura ejercitada: SQLite, JWKS, uvicorn, Chromium y tmux reales y descartables. Pact no aplica a un consumidor/proveedor versionados conjuntamente en este repositorio. No se sustituye la prueba del almacenamiento con mocks; el backend Work de aceptación y el límite de ejecución Docker figuran en completion-evidence.md.

Revisión independiente detectó problemas de precedencia, alcance de query token, navegación HTML, scopes ordenados, touch de peticiones fallidas, respuesta tardía de logout, validación de esquema, errores temporales y reservas del limitador. Se corrigieron con regresiones antes del cierre; resultados finales en completion-evidence.md.

Graphify se consultó antes de cambiar comportamiento y se contrastó con la fuente. Se decide diferir la regeneración global: el checkout contiene cientos de cambios ajenos y el grafo existente no representa la nueva implementación 004. No se lo certifica actualizado; las dependencias nuevas se verifican con fuente e Import Linter. No se duplica conocimiento en Obsidian.


Ajuste FR-024 revisado: bootstrap del propietario → BrowserSetup → /auth/setup con JWT exacto/admin/origen → account.lock y revalidación dentro del bloqueo → SQLite/pending config → cookie → config/session → dashboard. Concurrencia200/409 y recuperación de publicación interrumpida verificadas; sin ampliación de scopes, emisión Work o registro público. Auth y setup comparten política de cookie. Runtime activo verificado tras instalación y copia de recuperación. Riesgo excepcional de fsync posterior a rename documentado en completion-evidence.md.

T056 frontend navigation review: tab buttons cannot submit forms; busy state disables tab switching; unmount clears credential state; accountConfigured guard prevents posting login before first account; existing-account create view performs no registration call.402 Vitest and17 real browser cases plus activeDOM confirm preserved auth gates and dashboard transition.

Cierre2026-10-01: publicación incierta después de rename reconciliada con la misma validación del arranque; ninguna cookie en503, binding/account/config inválidos mantienen bloqueo. Recuperación frontend consulta por GET antes de cambiar de vista; sin replay automático del alta. Revisión final independiente no encontró bloqueantes de seguridad/composición y comprobó archivos centrales idénticos entre source y candidato aislado. La aceptación Docker previa del checkout incluyó composición managed ajena; el test entregado se ajusta al baseline /work-launches y ejecución Docker/bytes reales con validación del resultado mediante WorkService, con ese límite de transporte explícito.
