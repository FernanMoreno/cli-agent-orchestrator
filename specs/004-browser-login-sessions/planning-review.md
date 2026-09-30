# Revisión de planificación — 004

Fecha: 2026-09-30. Estado: planificación completa; implementación y aceptación runtime pendientes.

## Coherencia y cobertura

spec.md, plan.md, research.md, data-model.md, contracts/browser-auth.md, quickstart.md y tasks.md revisados conjuntamente. 48 tareas, IDs T001–T048 consecutivos, todas pendientes; US1=11, US2=9, US3=6, US4=8; setup/base/cierre=14. Cobertura explícita: 23/23 FR y 7/7 SC. Enlaces locales y marcadores de plantilla comprobados programáticamente. Sin contradicciones críticas detectadas en revisión documental.

Dependencias reales US1 -> US2 -> US3 -> US4; las pruebas independientes usan fixture preparada y no significan implementación independiente de requisitos previos. Casos E2E obligatorios están en quickstart y tareas; comandos nuevos están etiquetados futuros. Prioridad P1/P1/P2/P2 conservada. No se cambió spec.md ni su checklist revisado.

Correcciones incorporadas: límites capturados explícitamente por sesión; alta local puede verificar identidad con clave pública local con servidor parado; restore prepara client.env con ruta final, no staging; Work requiere preservar kind además del id y comprobar su contexto de recuperación, no asumir validez por copiar un archivo.

## Composition Review Report (diseño)

- **Subsistemas previstos**: web, resolución auth/API/WS/SSE, SQLite de sesión, tooling personal y restore. Ningún subsistema runtime modificado ahora.
- **Vecinos revisados**: security/auth, WorkAuthority/WorkRepository/provisioning, APIs de Work/knowledge, JWT/JWKS y clientes tradicionales, proxy Vite.
- **Invariantes del diseño**: identidad/scopes conservados, concesión distinta de duración de sesión, renovación sin Set-Cookie, revocación por epoch/version, SQLite autoritativa, origen exacto, no replay de writes, retiro de canales <=5 s, restore invalida antes de publicación.
- **Arquitectura ejecutada**: project-composition-check caos, exit 0; Import Linter analizó 329 archivos y 1320 dependencias; 5 contratos conservados, 0 rotos.
- **Contratos/escenarios runtime ejecutados**: ninguno de login 004, porque no está implementado. Acceptance con Playwright/API/SQLite/JWKS real y Work se exige en T020/T029/T035/T043/T046.
- **Dependencias reales ahora**: sólo arquitectura del checkout; futuras pruebas necesitan SQLite en disco, JWKS y browser reales. No presentar mocks ni health público como aceptación.
- **Riesgos pendientes**: coste/capacidad scrypt del equipo, persistencia real de cookie temporal/recordada, carreras UI/cookie, permisos durante stream silencioso, restore Work con contexto de archivo y proxy Vite exacto. Todos tienen tareas/verificación asignadas.
- **Veredicto**: PASS de revisión documental y arquitectura actual; feature runtime sin verificar y no implementada.

## Flujo y preservación

setup-plan.sh, setup-tasks.sh y check-prerequisites.sh --require-tasks --include-tasks ejecutados para 004. Checkout Git real main; los scripts muestran identificador 004 porque feature.json es el contexto activo. No existe extensions.yml: no hooks before/after plan/tasks aplicables. Graphify consultado y contrastado con fuente; su caché de consulta y memoria derivada se actualizan, sin reconstrucción estructural. No duplicar esta documentación en Obsidian. No commit, push ni despliegue.

Comprobaciones de fin: revisar contenido de archivos nuevos como diff de creación, git diff --no-index --check sin incidencias de whitespace, validar IDs/formato/cobertura/enlaces y prerequisite discovery. La salida 1 de diff --no-index puede indicar diferencias respecto a /dev/null, no un fallo runtime.
