# Quickstart y aceptación — 004

Guía de la implementación 004. Usar una instalación privada descartable para las pruebas. La comprobación de health no acredita login; los resultados ejecutados y sus límites figuran en [completion-evidence.md](completion-evidence.md).

## Prerrequisitos

Python/uv y Node/npm del proyecto, browser Playwright local, servidor con bundle construido y JWKS del despliegue personal; directorio privado de propietario OS. Para casos Work, worker ELF, checkout y Docker con imagen fija válidos siguiendo docs/personal-deployment.md. No llamar a proveedores de pago para probar auth. Reloj controlado sólo mediante fixture Python, nunca configuración web pública.

## Verificación automatizada

```bash
uv run pytest --no-cov test/security/test_browser_passwords.py test/clients/test_browser_auth_repository.py test/services/test_browser_auth.py test/api/test_browser_auth.py test/api/test_browser_channel_auth.py test/scripts/test_personal_deployment.py
uv run pytest --no-cov test/security/test_auth.py test/api/test_auth_read_gating.py test/api/test_ws_auth.py test/api/test_work_authority.py test/api/test_workflow_managed_ingress.py test/services/test_work_authority.py
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
project-composition-check "$(cat .ai/project-name)"
```

El runner E2E arranca FastAPI/JWKS autenticados en puertos temporales, servir el bundle real y comprobar procesos listos, no sustituirlos por mocks/rutas health. Fallos u omisiones se registran explícitamente; no contabilizar skipped como aceptación. Build escribe web_ui: revisar sólo cambios propios y no pisar artefactos previos.

## Preparación y uso manual

`CAO_PERSONAL_ROOT` apunta a instalación descartable ya inicializada mediante el tooling 003 y protegida con permisos 0700. La configuración web se realiza con el servicio activo y el enlace autorizado del propietario; backup/restore requieren parar el servicio según su lock. El alta por terminal es una alternativa; account-reset soporta servicio activo con transacción propia.

```bash
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-create --username operador
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" serve
```

En otra terminal:

```bash
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" web
```

En una instalación sin cuenta, abrir el enlace del operador desde el comando web y completar la pantalla de configuración integrada. La contraseña se introduce en el front. Tras crear la cuenta se abre el dashboard; las visitas siguientes usan el login sin copiar bearer. El alta por terminal mantiene su prompt privado como alternativa. Consultar un recurso protegido y operar un agente autorizado; comparar identidad y permisos con cliente bearer previo. No habilitar/provisionar Work como efecto del login.

## Matriz de escenarios obligatorios

| Caso | Ejecución | Resultado/evidencia |
|---|---|---|
| US1 / SC-001 | navegador limpio, usuario inexistente/password errónea, login correcto | dashboard sólo tras login; errores equivalentes; cuenta sin defaults/registro |
| FR-017 | diez fallos/300 s por cuenta o IP, esperar 60 s; concurrentes y usuario inexistente | 429 y Retry-After, reintento posterior posible, recuperación local accesible |
| US2 / SC-002 | reloj ocho horas, >=3 leases vencidos, dos pestañas, reload y restart CAO | cero prompts nuevos; identidad/receipts iguales; no replay de writes |
| US2 / SC-003 | contexto persistente recordado, cerrar proceso navegador/reabrir mismo perfil | cookie real conservada, acceso sin password |
| FR-006 / SC-003 | temporal: reload; cerrar sesión browser sin restaurar y abrir nueva | reload autorizado; cookie sin Max-Age/Expires; nueva sesión exige login |
| Temporal restaurada | restore ventanas/estado temporal | puede continuar sólo dentro de idle/absolute, nunca extender límites |
| FR-011 | controlar demora de renew/logout/401 viejo, login nuevo y dos pestañas | cookie nueva no borrada/sobrescrita; respuesta antigua no revoca estado nuevo |
| FR-018 | offline/restart después de login y durante POST | red distingue unavailable; cookie no borrada; escritura incierta sin replay |
| FR-021 | justo antes/después de idle/absolute, pestaña sólo polling, sleep, salto de reloj | renovar no revive; polling no mantiene vida; retroceso falla cerrado |
| US3 / SC-004 | dos contextos, logout actual y luego all; WS/SSE abiertos en silencio y con tráfico | actual no cierra otro; all cierra ambos; nuevo input/dato denegado y canales <=5 s |
| Origen ajeno | otro puerto local y origen remoto: form/login/renew/logout/GET/WS/SSE | rechazo antes de efecto; no lectura ni control; CORS tradicional no concede cookie |
| SC-005 | Work real con recibo y permisos preexistentes; renew/logout/red incierta | cero duplicados; misma tupla Principal incluido kind; job continúa |
| US4 / SC-006 | cambiar contraseña, reset local activo, backup y restore | vieja rechazada; sesiones previas revocadas; misma cuenta y grants; restore exige login |
| FR-003/019 | login disabled/IdP externo + CLI/TUI/MCP/bearer + Work no provisionado | políticas anteriores conservadas, sin alta de owner ni permisos nuevos |
| SC-007 | revisar document.cookie/storage/URL/redirects/422/logs/auditoría | ningún password o secreto de sesión accesible a JS o registrado |
| Almacenamiento | cookies/persistencia bloqueadas, privado, SQLite ocupado/caído | explicación; temporal si posible; 503 sin autorizar ni borrar sesión por red |
| Vite/BASE | bundle normal y proxy /auth explícito, prefix soportado | origen exacto, cookies coherentes, no CORS wildcard ni query token local |

No adjuntar valores de cookie/password ni capturas de formularios con secretos. Medir control en servidor desde commit de revocación hasta cierre de canal; comprobar también input y emisiones rechazadas, no sólo UI.

## Recuperación, restore y rollback

```bash
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-reset
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" backup "$CAO_AUTH_BACKUP"
python scripts/personal_deployment.py --root "$CAO_AUTH_RESTORED_ROOT" restore "$CAO_AUTH_BACKUP"
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-disable
```

Backup/restore requieren servicio parado y destino nuevo, según tooling existente. Fixture comprueba invalidación antes de publicar y falla sin destino usable si invalida falla. Disable conserva cuenta e identidad, revoca sesiones y mantiene JWT tradicional; para rollback de versión parar servidor y regresar al código anterior con login deshabilitado. Rehabilitar necesita nueva entrada; no restaurar sesiones revocadas por copiar DB.

## Evidencia de cierre

Registrar comandos/resultados, checks omitidos con motivo y tiempos de revocación en completion-evidence.md durante implementación. system-composition-review contrasta navegador/API/SQLite/Work/restore y gates; revisar diff y ejecutar verification-before-completion. Los resultados y los límites de aceptación se registran separadamente; esta matriz describe el procedimiento.

En este host Chromium necesitó `LD_LIBRARY_PATH=/tmp/cao-playwright-libs/root/usr/lib/x86_64-linux-gnu` por una biblioteca de audio ausente. No es configuración de producción. El desarrollo con Vite en otro puerto no admite cookies contra el origen canónico del backend; para login local servir el bundle construido desde CAO.
