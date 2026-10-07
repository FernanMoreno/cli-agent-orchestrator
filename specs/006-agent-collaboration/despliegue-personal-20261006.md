# Despliegue personal y aceptación real — 6 de octubre de 2026

## Objetivo y alcance

Aplicar la reconstrucción a la instalación personal existente y comprobar la colaboración de Claude, Codex y OpenCode en la misma PC, dentro del mismo contenedor Linux. El proyecto usado es `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`.

Esta comprobación continúa el [cierre anterior](cierre-20261005.md). Sus límites sobre instalación personal y acceso Docker desde WSL deben leerse con esta actualización.

## Operación realizada

1. Se revisaron el estado Git, las instrucciones aplicables de `AI_WORKFLOW.md`, el instalador y los manifiestos existentes. Se preservó el trabajo ajeno.
2. El servicio `cao-personal.service` estaba inactivo y no había un contenedor `cao-personal` en el motor disponible.
3. Docker Desktop tenía desactivada la integración con Ubuntu. Se conservó una copia privada de sus ajustes y se habilitó esa distribución. El comando normal de parada no finalizó; se examinaron sus procesos y registros y se reinició la aplicación Desktop. No se ejecutó `wsl --shutdown`.
4. Tras el arranque, el CLI Linux pudo acceder a `/var/run/docker.sock`. El motor no encontraba las imágenes CAO registradas anteriormente. Se reconstruyeron la aplicación y la imagen mínima de trabajadores con el instalador existente.
5. Antes de actualizar la referencia de la imagen de trabajadores se creó una copia privada completa del estado personal. Se comprobó que ese cambio conservaba la identidad de firma.
6. El instalador comprobó proveedores, integraciones, Docker Work y una instancia temporal antes del corte. Creó otra copia privada, inició la aplicación y verificó web, API y acceso autenticado del operador.
7. Se ejecutó una revisión real y de solo lectura del proyecto original, con perfiles exclusivos de esta prueba. Después se eliminaron la sesión y los archivos de perfiles propios.
8. Se reconstruyó la aplicación tras corregir el validador. La recreación final y la comprobación posterior se registran en la sección de resultados.

No se alteraron credenciales de proveedores, contraseñas ni archivos del demo. Los perfiles de prueba usaron nombres únicos. Las bases de datos conservan el historial normal de las operaciones realizadas.

## Prueba real de colaboración

Se reutilizó la lógica de `scripts/validate_collaboration_demo.py` mediante un adaptador privado de verificación. El adaptador apuntó a la API personal existente en lugar de iniciar otra API, utilizó un bearer firmado por la identidad personal y escribió perfiles temporales en su almacén. El MCP usado fue `/opt/venv/bin/cao-mcp-server`, dentro de la imagen.

El proyecto revisado fue el original registrado en los montajes, no una copia temporal. Los agentes tenían instrucciones de no modificar archivos ni ejecutar pruebas; las pruebas ejecutables posteriores las realizó el validador externo.

| Actor | Proveedor/modelo | Terminal de la ejecución aprobada |
|---|---|---|
| Supervisor | Claude Code / Sonnet | `80835873` |
| Revisión API | Codex / GPT-6.1-Sol | `031a7806` |
| Revisión frontend | OpenCode Go / GPT-6 Luna | `ef2e7829` |

Condiciones observadas:

- Claude delegó mediante CAO en dos trabajadores nativos diferentes.
- Ambos enviaron sus propios resultados mediante CAO; Claude produjo la síntesis final con recibo verificado.
- La segunda ronda reutilizó los mismos tres terminales.
- Cada trabajador completó una generación nueva con `RONDA2_DONE` y recibo vigente.
- Llegaron exactamente dos callbacks nuevos; Claude completó `RONDA2_FINAL`.
- El conjunto de archivos del proyecto y los archivos de implementación observados no cambiaron durante la ejecución.
- El proceso de prueba terminó con código 0 y `COLLABORATION=PASS`.

Los comentarios de los modelos sobre posibles defectos son hallazgos estáticos. No equivalen a fallos reproducidos: las pruebas HTTP y frontend del demo se ejecutaron por separado dentro de Docker y aprobaron.

## Carrera encontrada en el validador

La primera ejecución recibió los dos mensajes, pero el validador combinó esa observación con el estado `completed` del callback anterior. Consultó el resultado cuando el nuevo callback de Claude seguía en curso y obtuvo HTTP `202`. El registro mostraba la nueva entrega y el comienzo del trabajo de Claude; no demostraba un fallo del proveedor ni de entrega de mensajes.

La corrección se limita al validador:

- El perfil exige `COLLABORATION_REVIEW_FINAL` en la síntesis posterior a ambos callbacks.
- El bucle continúa hasta obtener el resultado `last` con HTTP 200, estado de turno verificado, generación vigente y ese marcador.
- Se reutiliza `verified_turn`; no se añade otra fuente de estado ni se cambian servicios de producción.
- Cuatro casos de regresión fallaron antes de añadir la función de comprobación. Después aprobaron los casos que rechazan HTTP 202, el primer callback incompleto y un turno sin verificar, además del caso final válido.

## Comprobaciones ejecutadas

| Comprobación | Resultado |
|---|---|
| Instalador Docker, regresiones | 30 pruebas aprobadas |
| Validador y escenarios de aceptación | 28 pruebas aprobadas; una advertencia de deprecación de Authlib |
| Arquitectura/composición después de la corrección | 5 contratos conservados, 0 rotos |
| HTTP real del demo dentro de Docker | 11 pruebas aprobadas |
| Frontend del demo con Node dentro de Docker | 13 pruebas aprobadas |
| Chromium, aplicación personal | Login renderizado; sin errores JavaScript; `/health` 200; `/sessions` sin bearer y con Origin válido 401 |
| Fuentes desplegadas | Diez módulos de producción comprobados mediante SHA-256 |
| Identidad y cuentas | Identidad de firma y filas de `browser_accounts` iguales a la copia anterior |
| Colaboración real personal | Dos rondas aprobadas, tres terminales conservados, dos callbacks nuevos |

Una primera petición del cliente de pruebas de Chromium omitió `Origin` y recibió 403 por el control de origen. Se corrigió esa petición de prueba; no se relajó la protección del servidor.

## Revisión de composición

**Superficies:** Docker Desktop/WSL, bind mounts, HOME del contenedor, estado personal, emisor de JWT, API, MCP, tmux y proveedores nativos.

**Contratos revisados:** acceso local del CLI al motor; UID y rutas de montajes; registro del proyecto; conservación de clave, identidad y cuentas; rechazo de solicitudes sin autenticación; carga real de MCP; identidad de emisor/receptor de los callbacks; orden entre entrega y síntesis; recibos vigentes; reutilización de trabajadores; limpieza limitada a recursos de prueba.

**Dependencias reales:** Docker Desktop, contenedor de aplicación, SQLite persistente, Chromium y los tres proveedores con sus logins existentes. El adaptador sustituye la creación de una API de pruebas por el servidor desplegado; no simula proveedores, mensajes ni recibos.

La revisión del incremento de código cubre exclusivamente la espera del validador y sus regresiones. No se modificaron módulos de producción ni el grafo de arquitectura. El informe del repositorio conserva la evidencia durable; no se duplica en el vault.

## Evidencia local

Registros bajo `/tmp/cao-spec006-close/`:

- `personal-install-build-20261006.log`, `personal-worker-build-20261006.log`: reconstrucción inicial y corte.
- `personal-providers-20261006.log`: primera ejecución y carrera reproducida.
- `personal-race-red.log`, `personal-race-green.log`, `personal-race-increment.diff`: regresión y revisión del cambio.
- `personal-providers-verified-20261006.log`: colaboración completa aprobada.
- `personal-candidate-rebuild-20261006.log`, `personal-install-final-20261006.log`: reconstrucción y recreación finales.
- `personal-browser-20261006.log`, `personal-browser-20261006.png`: comprobación de Chromium.
- `personal-demo-http-20261006.log`, `personal-demo-frontend-20261006.log`: pruebas del demo en Docker.
- `personal-composition-final-20261006.log`: gate de arquitectura.

El archivo de diagnóstico aprobado está en `/tmp/cao-investigation-evidence-20261005/cao-personal-proof-20261006-gva30ywi/`. Los registros temporales pueden desaparecer; los ajustes y el manifiesto privado de instalación permanecen en `~/.local/share/`.

## Límites que permanecen

- La autorrenovación de un bearer expirado dentro de un proceso MCP ya abierto sigue sin implementarse ni acreditarse. Esta operación de despliegue no modifica ese contrato.
- La recreación de un contenedor conserva estado y montajes; no demuestra que un proceso nativo/tmux sobreviva a la eliminación de ese contenedor.
- Los escenarios de fallo de proveedor, timeout, cancelación y reinicio con inbox pendiente siguen teniendo la evidencia WSL del cierre anterior. La nueva ejecución personal acredita colaboración normal con dos rondas.
- Los tres proveedores utilizan sus servicios de modelos y sus logins existentes. La coordinación y el estado de CAO son locales; la inferencia de los proveedores no es offline.

## Estado final

**Veredicto: PASS para despliegue personal y colaboración normal de dos rondas, con los límites anteriores.**

- `cao-personal` activo y saludable en `http://127.0.0.1:9889/`, publicado exclusivamente en loopback.
- Imagen final: `cao-spec006-candidate:20261005`, índice `sha256:4a82aa35a5b0e2cdf52a244d4f69a23f6122ec5f318229b5318e4fdd9b008864`.
- El manifiesto de ejecución Linux amd64 del contenedor probado y del contenedor final es idéntico: `sha256:84c9d9869c262b139af0585ee89037c74ade423bd36dcd248c16d2a64393d5ec`. La nueva construcción conserva el mismo runtime; el validador es una herramienta del host, no un script incluido en `/opt/cao/scripts`.
- Diez módulos de producción siguen coincidiendo por SHA-256 tras la recreación. El validador actualizado también coincide en el montaje del repositorio, cuyo destino está escrito en minúsculas en el manifiesto. Dentro del contenedor se respeta exactamente ese destino.
- Identidad de firma y filas de cuentas iguales a la copia anterior. El dato original del demo conserva SHA-256 `56e8dcaa7f8279fd8e9baca682808b84d707a66896e40b6a25444154ae3909ed`.
- Ninguno de los seis terminales creados en las dos ejecuciones queda accesible; cero procesos con sus IDs y cero archivos de sus perfiles de prueba.
- El contenedor anterior, `cao-personal-previous-a45c079a`, permanece detenido para recuperación. No se eliminó.
- Copia previa al ajuste: `~/.local/share/cao-personal-backup-20261006-before-image-update`.
- Copia del último corte: `~/.local/share/cao-personal-backup-20261006-011430-cc45cc`.
- La comprobación de Chromium posterior a la recreación vuelve a aprobar.

La evidencia agregada está en `/tmp/cao-spec006-close/personal-final-results-20261006.json`; las comprobaciones posteriores están en `personal-final-proof-20261006.log` y `personal-browser-final-20261006.log`.

Se cierran los pendientes operativos de despliegue personal e integración Docker–WSL. La autorrenovación MCP permanece pendiente. No se hicieron commits, pushes ni publicaciones.


## Actualización 2026-10-06: renovación MCP

El pendiente de autorrenovación del bearer local queda resuelto mediante la especificación 014. Implementación, prueba real con procesos abiertos, reconstrucción y conservación de cuentas se documentan en [el cierre de renovación](../014-local-mcp-token-renewal/cierre.md). Las observaciones anteriores se conservan como evidencia histórica.
