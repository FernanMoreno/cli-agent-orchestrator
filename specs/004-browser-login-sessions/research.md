# Research — 004 browser login sessions

Fecha: 2026-09-30. Decisiones de diseño, no evidencia de implementación.

## Evidencia del repositorio

- `security/auth.py`: is_auth_enabled depende de JWKS/IdP, no del navegador; get_current_scopes usa bearer; get_current_principal construye Principal sellado. `_verified_principal` deriva id de issuer/subject; `principal_from_token` usa kind=jwt. El módulo prohíbe imports de database/tmux.
- `services/work_authority.py` `_register` compara issuer/subject/kind del registro durable; `clients/work_authority_schema.py` admite jwt/local_operator. Usar kind=browser con id anterior rompería provisiones; usar subject=username crearía otro operador.
- `scripts/personal_deployment.py` initialize conserva issuer/subject en deployment.json, crea claves privadas y environment configura JWKS y token interno. restore copia estado y renueva client.env, sin invalidación de sesiones de navegador actual.
- `web/src/auth.ts` guarda bearer en sessionStorage, consume fragmento y lo borra ante 401; `web/src/api.ts` terminalSocketUrl lo envía en query. `BrowserConnection.tsx` presenta conexión por token.
- `api/main.py`: get_work_launch_principal valida bearer directamente; terminal_ws y agui_stream tienen verificadores propios. Cambiar sólo Depends(get_current_scopes) dejaría superficies sin login cookie. WS actual autoriza handshake, no revocación continua. OriginCheckMiddleware permite ausencia de Origin; cookie requiere política más estricta.
- `api/work_routes.py` y `api/knowledge_routes.py` reutilizan get_current_principal; workflow SSE en main.py y useEventFollow.ts conserva cursor. `web/vite.config.ts` no tiene proxy /auth.

Graphify: consulta BFS `auth principal scope token websocket stream origin work`, 354 nodos, salida limitada a 49. Se hallaron Principal (auth.py L452 del grafo), WorkAuthority, terminal_ws, OriginCheckMiddleware y work_routes. Fuente contrastada: auth.py Principal L445 y main.py terminal_ws L9106 en este workspace; no reutilizar números del grafo para edits. Lecciones existentes señalan WorkAuthority como fuente útil y un recorrido distinto de WorkAdmission como dead-end. No reconstruir grafo para escribir documentación.

## R1 — Sesión y renovación

**Decision**: secreto aleatorio de 32 bytes en cookie HttpOnly; sólo SHA-256 del secreto de alta entropía en SQLite. Sesión estable; concesión interna de acceso 3600 s renovada por POST /auth/renew sin Set-Cookie. Duración absoluta fija.
**Rationale**: autoridad revocable por DB, persistencia de servidor y ninguna renovación concurrente rota cookies ni depende de compartir secretos en JS.
**Alternatives considered**: JWT largo en navegador mantiene problemas de revocación y exposición; access/refresh cookies rotatorias requieren resolver Set-Cookie fuera del control de JS; JWT sólo en memoria añade coordinación innecesaria. No se aplica hashing rápido a contraseñas.

## R2 — Contraseña

**Decision**: stdlib hashlib.scrypt, N=131072, r=8, p=1, dklen=32, salt aleatoria >=16 bytes, maxmem explícito >=256 MiB. Verificación con compare_digest; máximo dos verificaciones simultáneas y cola acotada. Contraseña 10–128 caracteres, Unicode sin recortes/normalización silenciosa, cuerpo login <=4 KiB; username 1–64 caracteres ASCII alfanuméricos más ._- y normalización lowercase explícita. Usuario desconocido verifica hash dummy del mismo coste. Fallo de soporte/capacidad de scrypt bloquea habilitación; nunca fallback rápido.
**Rationale**: no nueva dependencia runtime y formato versionado para cambio futuro; ejecutar benchmark antes de habilitar y no reducir coste automáticamente. [Python hashlib](https://docs.python.org/3.10/library/hashlib.html) documenta scrypt; [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) respalda esos mínimos cuando no se usa Argon2id.
**Alternatives considered**: Argon2id es alternativa preferida por OWASP, pero requiere paquete nativo nuevo; PBKDF2 por compatibilidad no aporta ventaja aquí; SHA/bcrypt no elegidos para nueva cuenta.

## R3 — Identidad compatible

**Decision**: binding local validado contra el principal JWT personal (firma con clave pública local, issuer/audience/subject/exp configurados, incluso con servidor parado) y registros Work existentes, guardando issuer/subject/kind/scopes/principal_id; la fábrica de identidad sellada acepta binding sólo desde resolución autorizada de sesión en servidor. kind sigue jwt, auth_method se registra aparte. Comprobar binding contra configuración en cada resolución y fallar cerrado si cambia.
**Rationale**: conservar límites y tupla durable completa. La cuenta es un método de acceso al operador ya configurado, no un nuevo operador. Scopes efectivos nunca superan el binding local configurado; ningún campo web elige permisos. No importar infraestructura desde security/auth.py: puerto inyectado en composición HTTP.
**Alternatives considered**: nuevo kind exige migrar Work y rompe el alcance; reemitir JWT al browser devuelve secretos al cliente; sustituir IdP global afectaría CLI/MCP. Factoría pública de Principal permitiría falsificación.

## R4 — Origen y cookies

**Decision**: origen canónico explícito, cookie host-only/Path=/, HttpOnly/SameSite=Strict y nombre por instalación. Secure en HTTPS; loopback_http sólo para 127.0.0.1 local actual. Configuración inválida deshabilita servicio, no degrada política. POST exige Origin exacto y X-CAO-Browser; WS Origin exacto; GET cookie Origin exacto si existe o Sec-Fetch-Site=same-origin. No aceptar cookies de otro puerto/origen por CORS ni forwarded headers no confiables.
**Rationale**: cookies y SameSite no aíslan puertos. El servidor personal usa HTTP 127.0.0.1; no asumir que todos los navegadores aceptarán Secure en esa IP. [MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie) documenta flags, persistencia y restricciones.
**Alternatives considered**: depender sólo de SameSite/CORS permite efectos de orígenes locales distintos; imponer TLS como nueva infraestructura excede despliegue actual. Vite requiere origen de desarrollo aprobado explícitamente y proxy /auth; no wildcard.

## R5 — Persistencia, concurrencia y recuperación

**Decision**: DB de autenticación propia versionada y privada; epoch global y versión de cuenta hacen revocación/reset atómicos; transacciones cortas, sin caché positiva. Renovar/revocar nunca crean sesiones ni modifican cookie. Respuesta antigua sólo cambia UI si su generación sigue vigente y /auth/session confirma estado. Logout no emite borrado de cookie, evitando borrar el login nuevo. Logout repetido de secreto revocado es no-op.
**Rationale**: revocación manda en servidor aun con pestañas dormidas o avisos perdidos; la cookie residual no tiene autoridad. Independencia de DB Work permite rollback sin tocar trabajos. Reset/restore deben conservar cuenta, invalidar sesiones y auditar sin secretos.
**Alternatives considered**: estado sólo en memoria se pierde al reiniciar; rotar clave RSA invalidaría integraciones; borrar cookie en respuestas concurrentes introduce carreras.

## R6 — Canales, actividad y fallos

**Decision**: supervisión WS/SSE <=2 s desde DB y validación antes de cada efecto; concesión interna vencida desconecta y el cliente renueva/reconecta automáticamente sólo transporte. Tráfico útil actualiza actividad; heartbeat/polling automático/renew no lo hacen. GET protegido usa clasificación de polling fijada por servidor, no cabecera elegida por cliente. Reloj high-water persistido detecta retroceso y falla cerrado. Red/503 no revoca; desconectar canales ante fallo de almacenamiento protege control.
**Rationale**: no mantener autoridad de handshake después de logout ni mantener sesión viva eternamente por polling; no reintentar operaciones con resultado incierto.
**Alternatives considered**: TTL JWT por sí solo incumple cinco segundos; heartbeat renovador extiende inactividad sin actividad del usuario; confiar en broadcast del navegador no protege servidor.

## R7 — Aceptación y restauración

**Decision**: Playwright local contra bundle servido por FastAPI y JWKS/SQLite reales; reloj inyectable sólo en fixture de test, nunca endpoint web de producción. Restore en staging invalida sesiones antes de rename final; client.env debe contener rutas del destino definitivo. Crear/reset/disable desde propietario OS con getpass, locks y permisos, sin argv/env con secretos.
**Rationale**: jsdom no acredita HttpOnly, cookies de sesión/restauración ni carreras de red; snapshot en disco revela transacciones y reanudación real. Restaurar la misma identidad exige no rotar issuer/sub/clave.
**Alternatives considered**: health público no acredita login; sólo mocks ocultarían persistencia y canales; borrar toda la base rompería permisos/datos.

## Cierre de investigación

No quedan decisiones técnicas abiertas que bloqueen las tareas. Disponibilidad de navegador, coste scrypt, persistencia efectiva de cookies, proxy Vite y cierre WS/SSE son verificaciones obligatorias de implementación, no resultados asumidos. No existe aceptación runtime de esta feature todavía.
