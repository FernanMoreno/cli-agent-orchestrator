# Browser authentication contract — 004

Contrato propuesto, pendiente de implementar. Rutas relativas a BASE existente del bundle; `/sessions` conserva su significado de sesiones de agentes. Las nuevas son `/auth/*`.

## API

Todas las respuestas auth llevan Cache-Control: no-store; no incluyen secretos ni bodies rechazados. Login/contraseña usan DTOs y errores 422 redactados, sin el `input` de FastAPI. Cuerpo máximo 4 KiB para auth. Errores JSON: `detail: {code, message}` con códigos fijos. No serializar excepciones internas.

| Método/ruta | Entrada | Resultado | Autenticación |
|---|---|---|---|
| GET /auth/config | ninguna | 200 mode=local_password/bearer/disabled; remembered/temporal límites públicos | pública, no revela usuario/hash/binding |
| POST /auth/login | username, password, remember:boolean | 200 session DTO + única Set-Cookie | modo local enabled, Origin exacto, X-CAO-Browser: 1 |
| GET /auth/session | cookie | 200 session DTO incluso lease interno vencido | sesión vigente, guardia de origen/metadata; no refresh |
| POST /auth/renew | sin body | 200 session DTO con acceso renovado | sesión vigente, Origin + X-CAO-Browser; sin Set-Cookie |
| POST /auth/logout | sin body | 204 después de revocar actual; idempotente si secreto revocado | guardias browser, no fallback a identidad nueva; sin Set-Cookie |
| POST /auth/logout-all | sin body | 204 después de revocar todas | sesión vigente, guardias browser; sin Set-Cookie |
| POST /auth/password | current_password, new_password | 204; revoca todas y exige login | sesión vigente/acceso vigente + contraseña actual + guardias browser |

Session DTO: `session_id` no autenticante, `username`, `remembered`, `access_expires_at`, `idle_expires_at`, `absolute_expires_at`, `server_time`, `session_revision` (epoch/version sin secretos). No devolver cookie, digest, password material, JWT ni autoridad Work. Usar server_time para programación cliente; estado real lo decide servidor.

Errores: 401 `credentials_rejected` igual para usuario inexistente/contraseña errónea; 401 `session_required`, `session_expired`, `session_revoked`; 401 `access_renewal_required` sólo después de validar sesión aún vigente; 403 `browser_origin_denied` o permisos insuficientes según contrato existente; 429 `login_throttled` con Retry-After; 503 `auth_unavailable`/`clock_untrusted`; 404 rutas locales cuando modo no habilitado excepto /auth/config. Password actual errónea usa error genérico y limitador. Fallo de red se muestra «servidor no disponible» y no cambia autoridad.

## Cookie y selección de identidad

Nombre `cao_browser_<installation_id>`; valor aleatorio base64url de 32 bytes. Host-only (sin Domain), Path=/, HttpOnly, SameSite=Strict. Secure para HTTPS; excepción loopback_http configurada para origen canónico HTTP 127.0.0.1. No usar __Host- en HTTP. Recordada: Max-Age y Expires calculados hasta vencimiento absoluto; temporal: ambos ausentes. Nunca crear persistencia en local/sessionStorage ni URL. Cookie residual tras logout no autoriza; nueva entrada la sustituye.

Cookie sólo atendida si feature está habilitada y origen/socket/configuración es válido. No extender allowlist CORS compartida para hacer funcionar login. Cookies no aíslan puertos: validar también requests de lectura y handshake contra origen exacto. POST login y todos los efectos cookie requieren Origin exacto + X-CAO-Browser; rechazo antes de efecto incluso forms simples; Origin ausente/null rechazado. Lecturas cookie: Origin exacto si enviado; si ausente, Sec-Fetch-Site=same-origin. WS siempre Origin exacto.

Resolver credencial por solicitud: Authorization presente -> verificación JWT habitual y sin fallback cookie ante error; en ausencia -> cookie habilitada y validada; en ausencia de ambas -> comportamiento tradicional según instalación. En despliegue personal autenticado se rechaza acceso anónimo. Aplicar a require_any_scope/get_current_principal/get_work_launch_principal y verificadores directos; preservar envelopes Work existentes. Endpoints /auth/* no aceptan bearer como autorización de gestión de una sesión browser.

Identidad: tuple/scope de operador preexistente validada localmente y conservada; factoría sellada en security/auth.py, binding resuelto mediante puerto inyectado. `kind=jwt` mantiene clase del operador y registro Work; `auth_method=browser_session` sólo distingue transporte/auditoría. Sin emitir provisiones ni llamar a local_operator_principal en modo autenticado.

## Terminales y eventos

`/terminals/{id}/ws` admite cookie de browser con Origin exacto; URL sin token. Clientes nativos bearer y parámetros tradicionales conservan contrato cuando no operan en modo cookie; un credential explícito inválido nunca cae a cookie. WRITE/ADMIN continúa requerido para input. Validar sesión antes de aceptar, antes de enviar datos, antes de cada escritura PTY y periódicamente <=2 s con SQLite autoritativa. Código cierre 4401 para sesión inválida/lease vencido; 1013 almacenamiento temporalmente indisponible. No terminar tmux ni Work.

SSE workflow, AG-UI y MCP Apps cuando estén habilitados aceptan identidad cookie con scopes existentes; revisión periódica y antes de emitir cada evento; terminar stream ante pérdida de autoridad. Mantener cursores/replay y diferenciar autenticación de cursor. Sin access_token en URL para browser local. Lease vencido -> renovar y reabrir transporte; no repetir entradas ni entregas. Clientes bearer tradicionales no heredan política browser.

Para streams sin tráfico el monitor debe seguir activo. Renovar no toca last_activity. Polling de UI tampoco; endpoints/acciones cuentan actividad según clasificación de servidor. Estado UI entre pestañas usa avisos no secretos y /auth/session como fuente; la caída de BroadcastChannel/Web Locks no altera autoridad del servidor.

## Cliente web

Estados: initializing, anonymous, authenticated, renewing, unavailable, expired/revoked. Consultar /auth/config y /auth/session antes de montar vistas protegidas. Formulario username autocomplete=username; password autocomplete=current-password; recordar opcional sin marcar por defecto; mostrar vigencia. Cambio password autocomplete=new-password. No guardar passwords en estado persistido ni logs.

browserFetch usa credentials=same-origin, redirect=error y URL de origen idéntico; generaciones impiden que un 401 de solicitud anterior desmonte login nuevo. Sólo GET/HEAD se repiten una vez después de renovar. Antes de enviar escrituras se renueva si hace falta; tras timeout/desconexión no replay automático. Consultar recibo o estado y mostrar incertidumbre. Logout aborta fetch/streams del cliente y vacía vistas protegidas; red caída no declara revocación confirmada: mostrar cierre pendiente y reintentar sólo revocación.

Coordinación de login/logout con Web Locks cuando disponible; no es requisito de autorización. Tras respuesta tardía/broadcast de logout consultar /auth/session y no asumir que invalida una sesión nueva. Renews y logout no alteran cookies. Limpiar bearer histórico en modo local para no dejar secreto de continuidad en almacenamiento JS; enlaces antiguos no generan sesión recordada. Modo tradicional conserva conexión por bearer separado y explícito.

## Tooling local previsto

```bash
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-create --username operador
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-reset
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" account-disable
python scripts/personal_deployment.py --root "$CAO_PERSONAL_ROOT" web
```

Nombres son contrato futuro. account-create valida propietario OS/permisos/identidad existente mediante firma con clave pública local y claims configurados, sin depender de JWKS HTTP si el servicio está parado, solicita contraseña dos veces con getpass y habilita sólo al completar; cuenta existente no se sobrescribe. reset mantiene binding y revoca todas; disable revoca y vuelve a bearer sin desactivar JWKS. Ninguno acepta password por argumento/env ni emite secretos. Errores sin volcar config/clave. No ruta HTTP de create/reset local. Comando web en modo login abre URL normal sin fragmento bearer; fuera de él conserva comportamiento previo.

backup usa copia SQLite coherente y permisos actuales. restore invalida sesiones en staging antes de publicar; preparar client.env con rutas del destino definitivo, nunca del staging; conserva issuer/sub/claves/cuenta/datos/Work. Serve valida DB/schema/binding/origen antes de ofrecer login; no fallback anónimo cuando falla configuración habilitada. Comandos locales usan lock compatible con servicio/backup; reset con servicio activo modifica DB transaccionalmente sin bloquear recuperación por rate limit.
