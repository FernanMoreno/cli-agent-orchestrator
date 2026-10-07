# Tasks: renovación local MCP

## Preparación

- [x] T001 Diseño Spec Kit y análisis Graphify contrastado con fuente en `spec.md`, `plan.md`, `research.md` y `contracts/token-source.md`.

## US2 — Compatibilidad y seguridad

- [x] T002 Regresiones RED de fuente renovable, archivo inseguro y overrides en `test/security/test_local_token_file.py` y `test/utils/test_collaboration_mcp_environment.py`.
- [x] T003 Lector seguro y selección de credencial en `security/local_token.py`, `security/auth.py`; propagación y override en `utils/mcp_resolution.py`.

## US1 / US3 — Renovación sin reinicio

- [x] T004 Regresiones RED de publicación, TTL, ciclo y fallos en `test/scripts/test_personal_deployment.py`.
- [x] T005 Emisor único, archivo atómico y ciclo con parada en `scripts/personal_deployment.py`; eliminar workaround 12h en unit y `scripts/docker_personal_runtime.py`.
- [x] T006 Pruebas de security, MCP, perfiles y lifecycle; contratos HTTP-only, negativa de replay y arquitectura.

## Operación y cierre

- [x] T007 Reconstruir y desplegar conservando backup, cuentas, identidad y montajes; TTL=60 durante aceptación.
- [x] T008 Prueba real con tres proveedores: JWT anterior expirado 401, llamadas renovadas correctas, IDs y MCP PID/starttime conservados, fuente fallida sin fallback y recuperación sin replay.
- [x] T009 Restablecer TTL habitual; verificar instalación saludable, limpieza y datos; documentación en `docs/agent-collaboration.md` y `cierre.md`.
- [x] T010 Composición, revisión del diff, actualización AST focalizada, selector 008 preservado e informe final.

Dependencias: T001 → T002 → T003; T001 → T004 → T005; T003+T005 → T006 → T007 → T008 → T009 → T010. Sin delegación; no se requieren tareas independientes adicionales.

## Ampliación de aceptación autorizada 2026-10-06

- [x] T011 Rechazo de archivo inseguro y recuperación nativa con Codex y OpenCode, sin fallback, replay ni reinicio.
- [x] T012 Perfil explícito de Codex en ejecución real: bearer elegido y archivo heredado deshabilitado, llamada correcta con archivo global inválido.
- [x] T013 Sesión de dos horas reales a TTL habitual 3600 s: tres proveedores abiertos, llamadas periódicas, renovación y expiración observadas, mismos procesos.
- [x] T014 Limpieza propia, preservación del proyecto y documentación de resultados y límites.
