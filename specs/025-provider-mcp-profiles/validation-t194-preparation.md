# T194 · paquete Codex completo antes de mensajes con modelo

**Admisión y handshake nativo validados. Aceptación de mensajes T190 pendiente.**

El diagnóstico fijaba una copia del ejecutable Codex0.164 sin su auxiliar
`codex-code-mode-host`. El paquete instalado sí contiene ese binario y los
assets declarados. Es un defecto de aislamiento del lanzador; los controles
de versión/catálogo/MCP directo no ejercitan necesariamente ese host.
La [fuente fijada del resolver0.164](https://raw.githubusercontent.com/openai/codex/rust-v0.164.0/codex-rs/install-context/src/lib.rs)
deriva su ruta del paquete/ejecutable, y el
[proveedor de sesiones](https://raw.githubusercontent.com/openai/codex/rust-v0.164.0/codex-rs/code-mode/src/remote_session.rs)
rechaza su ausencia y lo arranca de forma diferida. Esto explica el mecanismo,
pero no acredita por sí solo la causa exacta de R4: su ruta nativa efectiva y
la invocación del host no se conservaron.

El nuevo lector comprueba un manifiesto privado vinculado por SHA independiente,
inventario completo de los directorios canónicos, metadata/layout/version
Linux0.164, binario/host/rg/bwrap, hashes, propietarios, modos, enlaces y límites.
No deriva la confianza de hashes reescritos por el propio snapshot. La integración
en el recorrido Codex comprueba también el origen del módulo y rechaza antes de
copiar credenciales o adquirir PostgreSQL si falta la admisión. El resto de
proveedores y los permisos no cambian; Code Mode permanece activo.

Entradas explícitas del lanzador:

```text
CAO_SPEC023_CODEX_RUNTIME_MANIFEST
CAO_SPEC023_CODEX_RUNTIME_SHA256
```

El selector ambiental legado `CODEX_CODE_MODE_HOST_PATH` se rechaza para mantener
cerrado el contexto del diagnóstico; no se afirma que0.164 lo interprete ni
que causara el fallo. La coincidencia de `/proc/<pid>/exe` con el paquete admitido
debe observarse antes de los triggers del próximo recorrido: el PATH del padre
por sí solo no es esa atestación.

Verificación:37pruebas nuevas PASS;210contratos previos PASS en la selección
combinada (241incluyendo las31nuevas iniciales), más20controles del observador
PASS:267distintas, sin sumar selecciones solapadas. Black/isort PASS,
arquitectura5contratos conservados/0rotos y revisión ClaudeGO en modo lectura.
El caso del recorrido real con rutas simuladas demuestra rechazo antes de auth/PG;
su limpieza está explícitamente doblada. Se corrigieron el contrato de ese doble
y el aislamiento del doble de admisión en la prueba previa de orden de perfiles;
los dos fallos iniciales de fixtures no se presentan como defectos de producto.

Se construyó un snapshot privado con43assets canónicos, todos comparados con
hashes fijados antes de copiar desde la instalación. El lector real lo admite.
El primer constructor incluyó por error el alias instalador raíz `codex -> bin/codex`,
fuera del layout canónico: fue rechazado antes de credenciales o ejecución y se
conserva como intento fallido. Se corrigió el constructor, no se relajó el lector.
No se tocó la instalación original ni se reinstaló nada globalmente.

Handshake nativo real PASS el2026-10-28 sobre el snapshot43assets: hello v1,
session/open, session/shutdown, EOF, salida0 y grupo propio retirado. Sin execute,
modelo, herramientas, credenciales ni red; aislamiento de mount/red y ausencia
de auth comprobados dentro del sandbox antes de exec. Admisión10848MiB frente
al mínimo6144. Tres tramas binarias archivadas0600 antes de parsear, protocolo
contrastado con la fuente oficial0.164; ejecutado archivado y descriptor operativo
re-cerrado, fuentes inalteradas. Negativo ENOENT del host del snapshot antiguo
sin ejecutarlo: no acredita la causalidad histórica de R4. Preflight real de
namespace/imports/firmas/pipes/plazo y revisión ClaudeGO antes de armar.

T194 cerrado para admisión/capacidad del host. T190 sigue abierto: nuevo recorrido
con paquete completo y fuente actual, dos envíos reales, PostgreSQL, AG-UI,
reinicio, auditoría/sello y limpieza, sin cambiar180s/6144MiB. Este control
sin modelo no acredita mensajes, autorización ni resultados del modelo.
