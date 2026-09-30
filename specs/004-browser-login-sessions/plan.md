# Implementation Plan: Login local y sesiones persistentes

**Branch**: `main` (checkout real; sin nueva rama) | **Date**: 2026-09-30 | **Spec**: [spec.md](spec.md)

**Contexto Spec Kit**: `004-browser-login-sessions`, resuelto por `.specify/feature.json`; el identificador devuelto por setup-plan no implica una rama Git nueva.
**Estado**: diseño ejecutado; validación y límites en [completion-evidence.md](completion-evidence.md).

## Summary

Añadir una cuenta local explícita al despliegue personal, vinculada a su operador actual. Una cookie opaca HttpOnly identifica una sesión persistida en SQLite; el servidor conserva una concesión interna de acceso de una hora, renovable mientras la sesión no venza ni esté revocada. Ningún bearer de navegador es necesario en este modo. Mantener el verificador JWT y las credenciales independientes de CLI/TUI/MCP/proveedores.

El diseño utiliza sesiones de servidor con identificador estable durante su vida. Renovar, consultar y revocar no escriben cookies: elimina la carrera de Set-Cookie entre renovación/logout y un login nuevo. Login siempre genera un secreto nuevo. La base de datos decide la vigencia en cada acción protegida; los canales abiertos consultan esa misma autoridad.

## Technical Context

**Language/Version**: Python >=3.10; TypeScript 5, React 18; versiones efectivas en pyproject.toml/uv.lock y web/package.json/package-lock.json.
**Primary Dependencies**: FastAPI, PyJWT y cryptography existentes; sqlite3, secrets y hashlib.scrypt de stdlib. Incorporar @playwright/test como dependencia de desarrollo de web para aceptación real; no añadir proveedor de identidad externo.
**Storage**: SQLite independiente `<deployment-root>/browser-auth.sqlite3`, esquema versionado, WAL, permisos 0600 y directorio 0700; identidad y Work permanecen en sus almacenes actuales.
**Testing**: pytest, Vitest, Playwright contra bundle construido + servidor real + JWKS personal + SQLite en disco; Import Linter y gate del proyecto.
**Target Platform**: instalación personal Linux/WSL, servidor y navegador locales, origen canónico `http://127.0.0.1:<server_port>`; desarrollo Vite mediante proxy explícito.
**Project Type**: servicio web y tooling local de despliegue.
**Performance Goals**: login cotidiano <30 s; retirar control WS/SSE <=5 s; acceso interno 3600 s; no verificación costosa de contraseña en consultas normales. Medir scrypt en el equipo y limitar a dos verificaciones simultáneas.
**Constraints**: una cuenta, opt-in, sin registro/reset público; los valores de FR-021 son límites de servidor configurables; sin replay automático de escrituras; misma identidad, scopes y provisiones; ninguna credencial de sesión accesible a JS.
**Scale/Scope**: propietario único con varias pestañas y navegadores; acceso remoto, múltiples usuarios y roles fuera de alcance.

## Constitution Check

| Principio | Antes de investigación | Después del diseño |
|---|---|---|
| I. Evidencia | Spec revisado, checkout y fuente inspeccionados | Decisiones trazadas en research.md; no afirmar implementación |
| II. Preservación | Workspace con cambios previos; no commits ni rama | Sólo artefactos de 004 y memoria derivada de Graphify; sin cambiar código |
| III. Diseño | Spec Kit plan y tasks autorizados | Impacto antes de implementar, TDD y dependencias en tasks.md |
| IV. Composición | Identificar browser/API/auth/Work/restore | Contratos, matriz de fallos y gates de implementación explícitos |
| V. Cierre | Verificación documental separada de runtime | Revisar artefactos; evidencias runtime registradas en completion-evidence.md |

Sin excepciones a la constitución. El spec ya existente satisface la fase de especificación; esta petición autoriza plan y tareas. No duplicar artefactos en Obsidian. No cambiar estructura de código ni reconstruir Graphify durante esta fase documental.

## Project Structure

```text
specs/004-browser-login-sessions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/browser-auth.md
└── tasks.md
```

Archivos previstos, no creados en esta fase:

```text
src/cli_agent_orchestrator/
├── security/auth.py                   # conservar JWT; resolver identidad verificada común
├── security/browser_passwords.py      # hashing y verificación sin infraestructura
├── models/browser_auth.py            # entidades/errores; sin imports de infraestructura
├── clients/browser_auth_repository.py # SQLite propia, migración, transacciones
├── services/browser_auth.py           # cuenta/sesión/política; usa puerto de identidad
├── api/browser_auth_routes.py         # /auth/*, cookies, CSRF, errores redactados
└── api/main.py                        # composición y canales existentes
scripts/personal_deployment.py         # account-create/reset/disable, configuración, restore, web
web/
├── src/auth.ts                       # estado, generaciones, renovación, fetch
├── src/components/BrowserLogin.tsx
├── src/components/BrowserAccount.tsx
├── src/components/BrowserConnection.tsx # conservar modo tradicional cuando corresponda
├── src/App.tsx
├── src/api.ts
├── src/components/TerminalView.tsx
├── src/components/workflow/useEventFollow.ts
├── vite.config.ts
├── playwright.config.ts
└── e2e/browser-login.spec.ts
```

**Structure Decision**: reutilizar límites actuales. `security/auth.py` no importa clients.database ni tmux: añadir resolución mediante puerto inyectado por composición API, conservando la independencia de MCP HTTP-only. Modelos no dependen de servicios/clientes. No extraer ni reestructurar main.py fuera de los adaptadores necesarios.

## Diseño y fases

### Phase 0 — Investigación cerrada

[research.md](research.md) compara alternativas y fija decisiones. Graphify descubre Principal/auth.py/WorkAuthority/terminal_ws/OriginCheckMiddleware; sus líneas están desactualizadas respecto al workspace. La fuente actual manda. El nuevo web/auth.ts y el despliegue personal no quedan acreditados por ese grafo.

### Phase 1 — Modelo y contratos

[data-model.md](data-model.md) define estados, tablas y transacciones. [contracts/browser-auth.md](contracts/browser-auth.md) fija endpoints, cookies, selección de credencial, canales y comandos. [quickstart.md](quickstart.md) define aceptación reproducible; sus comandos están implementados.

- Habilitar sólo con cuenta creada localmente, binding de identidad verificado y origen/configuración coherentes. No autohabilitar por detectar localhost, ni sustituir un IdP externo. Cambios de issuer/subject/scopes requieren intervención local; no regenerar propietarios.
- Conservar `(id, issuer, subject, kind=jwt, scopes)` del operador personal. La factoría sellada recibe únicamente el binding validado del servidor tras validar sesión/cuenta/configuración; `auth_method=browser_session` es metadata separada, no un nuevo kind de Work. No fabricar Principal en rutas ni aceptar identidad/scopes enviados por login.
- Cookie host-only, Path=/, HttpOnly, SameSite=Strict, nombre con identificador de instalación. Secure en HTTPS; excepción explícita `loopback_http` para el HTTP 127.0.0.1 ya existente, sin prefijos reservados Secure. Validar host, sockets y Origin exacto incluyendo puerto: las cookies no aíslan puertos. Rechazar cualquier otro origen incluso si CORS tradicional lo permite. GET cookie requiere Origin exacto si presente o Fetch Metadata same-origin; WS requiere Origin exacto.
- POST cookie requiere Origin exacto y cabecera `X-CAO-Browser: 1`; login también. No token CSRF secreto en JS. Fetch Metadata refuerza, no reemplaza, el control de origen. Bearer explícito válido conserva reglas tradicionales y nunca cae a cookie si es inválido.
- Cookie recordada expira en duración absoluta, no se reemite al renovar; temporal omite Expires/Max-Age. Renovación de acceso es estado en servidor, no rotación del secreto de sesión ni ampliación del plazo absoluto.
- Inactividad sólo avanza por solicitudes funcionales válidas; polling/renovación/heartbeat no la extienden. Datos de terminal enviados/recibidos cuentan como actividad. Monitor de canales cada <=2 s, revisión antes de emitir datos y antes de inyectar entrada; retirar canal ante revocación, vencimiento o almacenamiento indisponible sin matar agente/Work.
- Respuestas tardías no cambian cookies excepto login explícito. Logout deja una cookie sin autoridad hasta su expiración; no emite borrado tardío. Cliente usa generación local y confirmación /auth/session antes de cambiar UI; mensajes entre pestañas contienen sólo avisos, nunca credenciales.
- Renovar preventivamente antes de cada escritura; si falla después de enviar una escritura, mostrar resultado incierto y consultar recibo/estado, sin replay. Sólo GET/HEAD pueden repetirse una vez tras `access_renewal_required`; un 401 JWT tradicional no activa login local.

### Phase 2 — Tareas y entrega incremental

Generar tasks.md por US1–US4, pruebas antes del comportamiento y checkpoints independientes. Base + US1 es MVP de login para demostración local; la feature requiere completar todas las historias. US2 añade continuidad y canales, US3 revocación, US4 recuperación/restore/compatibilidad; todas dependen del mismo contrato fundacional.

## Migración reversible y fallos

Esquema separado aditivo con versión. Crear cuenta con control de propietario del directorio, sin contraseña por argv/env; prompts getpass con confirmación. Escritura atómica de configuración + prevalidación del binding; si falla, dejar modo deshabilitado y permitir reintento sin duplicar cuenta. Estado de cuenta válida con feature deshabilitada nunca concede cookies.

SQLite usa transacciones cortas BEGIN IMMEDIATE para renovación/revocación y comparación de auth_epoch/account_version. Verificar contraseña fuera del bloqueo y comparar versión al insertar sesión: un reset concurrente gana. Sin caché positiva de revocación. SQLite indisponible produce 503 y no elimina estado cliente ni autoriza canales. Al retroceder el reloj, fallar cerrado para sesiones hasta volver a un tiempo >= high-water persistido; adelantar reloj puede vencer sesiones, nunca revivirlas.

Restore debe copiar a staging, invalidar todas las sesiones e incrementar epoch antes de publicar destino y preparar client.env con rutas del destino definitivo, nunca del staging. Preserva claves/issuer/subject/cuenta y Work; no invalida JWT de integraciones por rotar la clave del emisor. Un fallo mantiene destino sin publicar. Rollback: parar servidor, deshabilitar login en configuración y revocar sesiones conservando cuenta/Work; regresar al bearer existente sin bajar autenticación. Rehabilitar exige nueva entrada. No downgrade destructivo de la DB de Work. WorkRepository verifica identidad de archivo/contexto inbox: un restore a otra ruta debe ejercer su procedimiento existente y probar provisiones reales; copiar una tabla de demostración no acredita recuperación Work. No rediseñar esa recuperación dentro de 004.

## Validación y cierre de implementación

FR-020 exige navegador real y servidor autenticado. Vitest/TestClient solos son insuficientes. Probar tres renovaciones en jornada de ocho horas con reloj controlado, cookies reales, contextos persistentes, reinicio de servidor, fallos de red, renovación/logout retrasados, inactividad, relojes y restore. Comparar recibos/provisiones antes/después. Revisar logs/respuestas 422 y traza de URLs sin conservar secretos.

Ejecutar pytest de los nuevos módulos y regresiones auth/Work/personal, Vitest, build y Playwright; Import Linter (a través del gate configurado) y `project-composition-check "$(cat .ai/project-name)"`. Composición de browser/API está en tests de contrato compartido; Pact nuevo no es necesario porque bundle y API se entregan juntos, pero mantener contratos existentes. Usar SQLite real y JWKS local, no mocks para transacciones/reinicio. Docker real aplica a compatibilidad Work, no a hashing/cookies.

Cierre: system-composition-review, diff de archivos propios, decisión de refresco Graphify si cambió estructura, no duplicación en bóveda, verification-before-completion. No afirmar runtime probado durante planificación.

## Complexity Tracking

Sin violaciones constitucionales. DB separada reduce alcance de migración; concesión interna de una hora permite comprobar renovación sin exponer JWT ni añadir un protocolo OAuth nuevo. Identificador de sesión estable evita carreras de cookies; su revocabilidad y límites de vida son autoridad del servidor.
