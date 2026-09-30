# Data model — 004

Estado propuesto; ninguna tabla creada todavía. DB propia `browser-auth.sqlite3` bajo raíz privada del despliegue. No migrar tablas de Work ni reinterpretar sus grants.

## Entidades y tablas

| Tabla | Campos principales | Restricciones |
|---|---|---|
| auth_metadata | singleton_id=1, schema_version=1, installation_id, auth_epoch, time_high_water | versión soportada, epoch creciente, id estable por instalación |
| browser_accounts | id, username_normalized, password_scheme, password_salt, password_hash, password_params, account_version, enabled, principal_id, issuer, subject, principal_kind, scopes_json, created_at, password_changed_at | una fila; username único; kind=jwt para binding personal; id/tupla idénticos al operador; hash scrypt versionado |
| browser_sessions | id, account_id FK, secret_digest UNIQUE, auth_epoch, account_version, remembered, idle_seconds, absolute_seconds, created_at, last_activity_at, absolute_expires_at, access_expires_at, revoked_at, revoke_reason | secreto nunca almacenado; fechas UTC; access <= absolute; índices de digest/account; account/epoch/version coherentes |
| browser_login_limits | bucket_digest, bucket_kind, window_started_at, failures, blocked_until, updated_at | claves acotadas, dos buckets independientes cuenta/IP real; origen autorizado se valida antes; limpieza por TTL |
| browser_auth_events | id, occurred_at, event_kind, outcome_code, account_id nullable, session_id nullable, peer_category, auth_method | sin password, cookie, hash, cabeceras ni bodies; retención configurable |

Eventos: login_success/failure, renew_failure, logout, revoke_all, password_change, local_recovery, restore_invalidate, local_disable. `session_id` es identificador público no autenticante distinto del secreto. En auditoría nunca registrar username arbitrario ni IP/URL sin sanitizar. Valores de buckets usan digest del username normalizado (también inexistente) e IP de socket; no confiar en X-Forwarded-For.

## Configuración local

En deployment.json: browser_login.enabled, installation_id, canonical_origin, transport_policy, temporal_idle_seconds=28800, temporal_absolute_seconds=86400, remembered_idle_seconds=604800, remembered_absolute_seconds=2592000, access_seconds=3600. Valores positivos, access no mayor que límites absolutos, idle <= absolute. DB path derivado de raíz privada, nunca cuerpo HTTP. Capturar idle_seconds y absolute_seconds por sesión al crearla; cambios de política deben aplicar el mínimo entre valor capturado y configuración actual, nunca extender duración de una sesión existente.

Bindings se obtienen del operador verificado del despliegue existente; jamás username como subject. Cuenta enabled=false o feature deshabilitada rechazan cookies; autenticación bearer sigue operativa. No permitir rebind silencioso tras cambiar configuración.

## Estados y transiciones

Cuenta: inexistente -> creada/deshabilitada -> habilitada con opt-in local. Cambio/reset incrementa account_version y revoca todas las sesiones en una transacción. No crea sesión automáticamente. Deshabilitar revoca y conserva cuenta/hash/binding.

Sesión: creación por login correcto -> acceso vigente -> acceso interno vencido/renovable -> acceso vigente. Desde cualquier estado autorizado -> revocada o caducada, terminales sin salida. Una sesión inválida nunca vuelve a autorizada; login crea otra fila/secreto. `access_expires_at` vencido no equivale a sesión caducada.

Vigencia: enabled + epoch/version coincidentes + revoked_at NULL + now < absolute_expires_at + now < last_activity_at + idle_seconds. Resolver acción además exige now < access_expires_at y permisos actuales. `POST /auth/renew` acepta sesión vigente aunque acceso interno haya vencido y fija access_expires_at=min(now+access_seconds, absolute_expires_at, idle_deadline). /auth/session no extiende actividad ni acceso.

Sesión recordada fija cookie Max-Age/Expires al plazo absoluto inicial; temporal no tiene persistencia explícita. Restauración de ventanas puede restaurar cookie temporal; límites de servidor siguen aplicándose. Cookie bloqueada produce estado sin sesión tras login y explicación de persistencia; no fallback con secreto en JS.

## Transacciones e invariantes

1. Login: comprobar límites en transacción corta, reservar capacidad de verificación, comparar hash fuera de lock; BEGIN IMMEDIATE revalida enabled/version/epoch/binding/limiter antes de insertar sesión y auditoría. Intentos concurrentes no superan threshold. Fallo no publica cookie.
2. Renovación: lookup por digest, revalidar todos los límites bajo BEGIN IMMEDIATE, actualizar sólo concesión de acceso. Repetir es seguro y no produce cookie. Logout/reset gana por epoch/version/revoked_at. No ampliar scopes ni último uso por renovar.
3. Acción protegida: resolver estado sin caché; chequear antes de admitir efecto. Revocar impide acciones admitidas después del commit; las admitidas antes pueden finalizar y mantienen recibos. No pretender cancelar trabajo durable ya admitido.
4. Logout actual: UPDATE de sesión identificada, evento atómico; secreto revocado produce no-op. Todas: auth_epoch creciente + sesiones revocadas + evento; confirmar respuesta sólo tras commit. Contraseña: hash nuevo + account_version + revocaciones + evento atómicos.
5. Restauración: copiar snapshot en staging, migrar esquema soportado, incrementar epoch y revocar todas las filas, evento; validar integridad y permisos, preparar client.env para la ruta definitiva (no staging) y publicar por rename final. No publicar destino si invalida fallida. Ningún cambio de identidad/grants/recibos.
6. SQLite real con WAL, foreign_keys ON, busy_timeout acotado; locks agotados/storage fallido -> 503, nunca acceso optimista. Cierre canales en fallo sin borrar cookie cliente.
7. now usa reloj UTC inyectable sólo internamente. Comparar high-water en operación autorizante; retroceso genera clock_untrusted hasta recuperación local/tiempo >=high-water, jamás decrementarlo. Saltos hacia delante vencen sesión. Tests incluyen reinicio y ambos saltos.
8. Diez fallos en ventana de 300 s en bucket cuenta o IP imponen espera 60 s; 429 incluye Retry-After. Login correcto no desbloquea otros buckets. Limitar total de buckets y expirar datos viejos. Recuperación local ignora rate limit y puede limpiarlo de manera auditada.

Retención: eliminar sesiones vencidas/revocadas tras periodo configurable y purgar buckets viejos; no usar fila eliminada como sesión válida. Copias no guardan cookie/secreto; sólo digest. Password hash/salt privados incluidos en backup de propietario, nunca logs o respuestas.
