# Tasks: Instalación Docker integrada

## Phase 1 — Diseño y contratos

- [x] T001 Crear spec/plan/research/data-model/contracts/quickstart en specs/005-docker-installation/.
- [x] T002 Verificar impacto Graphify y fronteras de source; registrar decisión TDD.

## Phase 2 — US1 Imagen de aplicación (P1)

- [x] T003 Escribir y ejecutar tests red de supervisión/proxy en test/scripts/test_docker_install.py.
- [x] T004 Implementar docker/personal/Dockerfile y contexto allowlist; compilar frontend y MCP antes del wheel, providers y stage worker-rootfs.
- [x] T005 Implementar scripts/docker_personal_runtime.py con proxy loopback, señales y renovación; verificar imagen real aislada.

## Phase 3 — US2 Instalación/migración automática (P1)

- [x] T006 Escribir tests red de montaje, instalación repetida y rollback en test/scripts/test_docker_install.py.
- [x] T007 Implementar scripts/docker_install.py e install.sh: prerrequisitos, build, init, preflight, backup, corte, salud, deshabilitar host tras éxito.
- [x] T008 Verificar installation nueva/repetida y rollback con contenedores reales de prueba.

## Phase 4 — US3 Integraciones/persistencia (P2)

- [x] T009 Verificar providers existentes, rootfs Work, paths persistidos, cuenta/sesiones/issuer y API protegida.
- [x] T010 Ejecutar migración personal autorizada y prueba real stop/start conservando datos.

## Phase 5 — Cierre

- [x] T011 Documentar instalación/ciclo de vida/rollback/recuperación en docs/docker-installation.md y enlazar README/personal-deployment.
- [x] T012 Pruebas aplicables, composición, revisión de contratos/diff y verificación final; registrar resultados reales.

## Dependencies & execution

T001→T002→T003→T004/T005→T006→T007→T008→T009→T010→T011→T012.
Builds de web/MCP y pruebas de contratos pueden ejecutarse en paralelo; corte/migración son secuenciales.
MVP: imagen real con estado nuevo; después installer reversible e integraciones; último corte personal con backup.

- [x] T013 Preserve Windows MCP via an owner-only Unix socket stdio relay; test exact-command authorization, forwarding, and Playwright paths before migration.
