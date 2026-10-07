# Instalación Docker integrada — Implementation Plan

**Branch**: workspace main | **Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)
**Goal**: desplegar backend/frontend/login desde un contenedor y automatizar instalación/migración reversible.
**Architecture**: wheel Python con web_ui compilado; personal_deployment sirve API/JWKS loopback. nginx dentro del mismo contenedor escucha 8080 y hace proxy hacia el puerto personal existente, elimina forwarded headers y soporta WebSocket/SSE. Docker publica exclusivamente 127.0.0.1:puerto:8080. Datos, HOME de runtime e integraciones se montan explícitamente.
**Tech Stack**: Python 3.12, uv lock, Node 24/npm lock, nginx, Docker Engine >=25 Linux amd64, SQLite, pytest.

## Technical Context

- Aplicación FastAPI/React existente. No modificación de autenticación o esquemas.
- Imagen multistage: web/MCP build, wheel uv frozen, providers Claude/Codex fijados; stage worker-rootfs scratch con supervisor C existente.
- Instalador Python stdlib y wrapper shell: check/build/init/preflight/backup/stop/start/verify/disable-old. Sin dependencia Python del host para ejecutar el servicio.
- Compatibilidad: montaje de root en la misma ruta absoluta, UID/GID del propietario; issuer/JWKS e IDs no cambian. HOME privado de runtime más montajes de proveedores/workspaces.
- Contenedor supervisa ambos procesos, termina ante fallo, propaga señales y rota cada 12h para renovar JWT interno. Docker restart unless-stopped.
- Imagen worker preservada por digest y source SHA; no se confunde con imagen de aplicación.
- Pruebas: pytest de instalador y rollback, imagen Docker real, pruebas HTTP/login/SSE/WebSocket y stop/restart. Arquitectura/composición existentes.
- Rendimiento: proxy sin buffering SSE; salud converge en <=120s; backup SQLite consistente antes de corte.
- Scope: aplicación local única; Linux/WSL con Docker activo; no publicación de registry ni cambios globales en daemon.

## Constitution Check

I código/ejecución verifican decisiones; II owner diff conservado; III spec-plan-tasks-Graphify antes de implementación; IV contratos, datos y rollback reales; V resultados honestos. PASS antes/después del diseño.

## Project Structure

- docker/personal/Dockerfile, Dockerfile.dockerignore: build autocontenido y contexto sin secretos.
- scripts/docker_personal_runtime.py: supervisión y configuración nginx en runtime.
- scripts/docker_install.py: installer stdlib, manifest privado, comandos de ciclo de vida.
- install.sh: entrada Linux/WSL desde checkout, sin sudo implícito.
- test/scripts/test_docker_install.py: transición, idempotencia, fallo y montaje.
- docs/docker-installation.md, specs/005-docker-installation/: instrucciones/contratos/evidencia.

## Execution and TDD

Primero tests que fallen por módulo ausente/contrato de instalación; después runtime/build/installer. Docker real valida boundaries que mocks no cubren. Migración personal sólo tras preflight aislado con raíz privada de prueba. Parar servicio, backup consistente, arrancar, validar identidad/DB, deshabilitar host tras éxito; rollback recupera modo anterior sin restore().

## Complexity Tracking

Proxy nginx necesario porque bridge entrega peer no-loopback y login requiere loopback real; no se relajan guardas. No base de datos ni frontend server adicionales. Reutilizar root elimina una migración de identidad innecesaria.

### Compatibility discovered during executable preflight

The existing Codex MCP configuration starts Roblox Studio with Windows `cmd.exe`.
Linux containers cannot execute WSL interop binaries directly. Preserve this explicit
external integration through an owner-only Unix socket relay on the WSL host, with
an exact command/argument allowlist from the existing configuration. The relay
serves no web/API ports; all CAO frontend/backend/issuer processes remain in the
application container. Mount existing Playwright browser/library paths read-only.
Verify allowlist rejection and actual stdio forwarding before migration.
