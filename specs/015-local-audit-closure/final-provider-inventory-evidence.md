# Inventario final de proveedores reales — 2026-10-07

Se revisaron **los 13 adaptadores reales registrados**, excluyendo `mock_cli`,
contra el registro de `src/cli_agent_orchestrator/providers/catalog.py` y la
instalación local. Los 13 comandos están presentes; Hermes no resulta
ejecutable porque su launcher refiere a un intérprete de una carpeta temporal
eliminada (`--version` termina con exit 127). La instalación no acredita login,
modelo disponible ni aprobación de una celda.

La evidencia anterior de **9/9 celdas aprobadas** entre Codex, Claude y OpenCode
se conserva en [real-provider-evidence.md](real-provider-evidence.md), incluidos
los dos fallos históricos de Claude y su repetición tras renovar el login.
No se encontró otro endpoint con CLI operativa, autenticación reutilizable
revisada y modelo exacto configurado que pudiera añadirse sin elegir un modelo
nuevo, cambiar proveedor de inferencia o iniciar otro login.

| Adaptador | CLI / versión | Modelo configurado revisado | Resultado de preflight |
|---|---|---|---|
| `kiro_cli` | `kiro-cli` 2.27.1 | No configurado | `auth_kv` contiene 0 registros; `whoami` aislado devuelve exit 1 y `Not logged in`. |
| `claude_code` | `claude` 2.1.292 | `sonnet` | Login reutilizable; celdas reales anteriores aprobadas. |
| `codex` | `codex` 0.160.1 | `gpt-6.1-sol` | Login reutilizable; celdas reales anteriores aprobadas. |
| `hermes` | Launcher presente; exit 127 | `anthropic/claude-opus-4.6` | Intérprete ausente. `model.provider=auto`; estados de proveedores vacíos, sin API keys. La única entrada del pool corresponde a Copilot; no demuestra disponibilidad del endpoint configurado. |
| `kimi_cli` | `kimi` 2.1.1 | No configurado | `.kimi-code/config.toml` y directorio `credentials` ausentes. |
| `copilot_cli` | `copilot` 1.0.91 | No configurado | Configuración contiene únicamente `firstLaunchAt`, sin `copilotTokens`; registros heredados de hosts/apps ausentes; sin variables de token GitHub. No se revisó una cuenta/modelo para esta CLI. |
| `opencode_cli` | `opencode` 2.0.18 | `opencode-go/longcat-2.5-preview-free` | Login reutilizable; celdas reales anteriores aprobadas. |
| `cursor_cli` | `agent` 2026.10.01-e373342 | No configurado | Con copia privada de su configuración, `agent status` termina con exit 0 pero dice `Not logged in`; ese exit no cuenta como autenticación ni PASS. |
| `antigravity_cli` | `agy` 1.2.15 | No revisado | Con copia privada del estado, `agy models` devuelve exit 1 y solicita iniciar sesión. El estado existente aporta metadatos de instalación/onboarding. |
| `gemini_cli` | `gemini` 0.62.0 | No configurado | Sin `settings.json`, `oauth_creds.json`, `google_accounts.json` ni variables de API key. |
| `omp` | `omp` 18.4.12 | No configurado | Inspección SQLite de solo lectura: `auth_credentials=0`, `settings=0`; sin variables de API key. |
| `grok_cli` | `grok` 1.0.46 | No configurado | `.grok/auth.json` ausente; configuración contiene únicamente secciones CLI/marketplace; sin API key. |
| `mcode` | `mcode` 0.6.2 | `minimax/MiniMax-M3.1-Flash-Preview` | Modelo explícito en `defaultModel`; sin `local-runtime.auth.json` ni `cli-auth`; directorio `auth` contiene únicamente un lock; sin API key. |

Las rutas Kimi y MiniMax se contrastaron con los constructores de hogares
reales (`kimi_runtime_home.py` y `_copy_auth_material` de `minimax_code.py`).
Grok utiliza `auth.json` en su hogar. Se consultaron solamente nombres de
campos permitidos, identificadores de modelos/proveedores, existencia de
registros y recuentos de filas; no se publicaron valores de credenciales ni
identidades de cuentas. Los probes de versión y ayuda emplearon HOME
transitorios; los probes de estado reutilizaron exclusivamente copias privadas
de los registros indicados y no iniciaron login.

## Cuota real y continuidad

Se ejecutó una comprobación real adicional de las tres celdas del mismo
proveedor con `CAO_REAL_PROVIDER_E2E_QUOTA_OBSERVATION_SECONDS=30`, conservando
los tres modelos anteriores. El límite permite observar el mismo turno si el
proveedor presenta espontáneamente una pausa; no autoriza reenvío ni
agotamiento deliberado de cuenta.

Resultado: **3 passed, 0 failed, 0 skipped**, exit 0, **191.819 s**.
Codex → Codex: 64.409 s; Claude → Claude: 78.457 s; OpenCode → OpenCode:
48.621 s. Cada celda volvió a acreditar turno con resultado y recibo, entrega
entre hermanos, cancelación con limpieza, reconciliación después de timeout y
limpieza de sesión.

Ningún proveedor presentó una pausa real durante esta ejecución. **La pausa y
la continuación por cuota siguen sin prueba de aceptación real**. Las tres
celdas registraron ambos escenarios como `skipped/not_applicable`; no se
inició el reloj de observación y sus campos permanecen nulos en JUnit. Un turno que
responde normalmente, un skip, la configuración de 30 segundos o las pruebas simuladas
del contrato no acreditan esos escenarios. No se consumieron créditos para
provocar agotamiento, no se cambiaron modelos ni se reenviaron turnos pausados.

## Reproducción y límites

La matriz adicional utiliza `/home/felni/cao015-final/candidate`, copia Linux
nativa con entorno Python 3.12 y fuentes del workspace contrastadas por hash.
Se declaran los tres endpoints y los tres filtros del mismo proveedor,
`CAO_RUN_LIVE_PROVIDER_TESTS=1`, `CAO_REAL_PROVIDER_E2E_STRICT=1` y un
`CAO_REAL_PROVIDER_E2E_AUTH_HOME` absoluto privado, distinto tanto del HOME del
operador como del HOME temporal del runner. Se copian únicamente los registros
de login declarados con modo 0600; cada celda obtiene servidor, HOME, SQLite y
terminales propios; el socket tmux utiliza su raíz temporal privada. Se retiraron
las variables heredadas de claves, tokens y rutas de configuración. Al terminar
se eliminaron el servidor tmux aislado, los HOME y todas las copias privadas de
autenticación; la comprobación de ausencia del directorio temporal fue positiva.

Logs privados, probes y JUnit: `/home/felni/cao015-final-providers/`. Los logs de
proveedores no se incorporan al repositorio. El inventario publicable está en
[final-provider-inventory-results.json](final-provider-inventory-results.json).
Los diez endpoints bloqueados no se cuentan como aprobados ni como celdas
reales ejecutadas. Requieren sus propios modelos exactos y registros de login
reutilizables revisados; Hermes requiere además reparar su instalación.

El usuario confirmó que no dispone de las cuentas restantes y que por ahora
no se pueden probar esos diez proveedores. Se conserva esta limitación externa
en la aceptación; no quedan pendientes más solicitudes de login en esta fase.

## Repetición final tras cierre SQLite — 2026-10-07

Después de las correcciones T080 de propiedad/cierre determinista SQLite y su
guarda de setup, se repitieron únicamente las tres celdas reales del mismo
proveedor contra el candidato final congelado: **3 passed, 0 failed,
0 skipped**, exit 0, **186.043 s**. Se conservaron los modelos exactos
y el aislamiento anteriores. Duraciones: `codex->codex`: 55.471 s; `claude_code->claude_code`: 79.398 s; `opencode_cli->opencode_cli`: 51.002 s.

Cada celda volvió a acreditar resultado de turno, recibo `succeeded` y join,
entrega persistida entre hermanos, cancelación con limpieza, conservación de
`reconcile` tras timeout y eliminación de sesión. Se detuvieron los servidores
CAO, se eliminó el socket/servidor tmux privado y desaparecieron los HOME,
perfiles temporales y copias de autenticación contenidos en la raíz privada.
La prueba verifica limpieza de sesión/recibos y eliminación de esos recursos;
no se presenta como una aserción independiente sobre leases persistentes de
perfiles fuera de dicho ámbito.

El manifiesto final contiene **1335 registros** y tiene SHA-256
`fd1d17a9bd9159a2d6f59ce7342a60dd16408f3fb21d322036d75b2a7398a21a`. Se contrastaron además
ocho hashes actuales entre candidato y workspace: harness, fixture, tres
adaptadores, `database.py`, `workflow_journal.py` y `workflow_spec_service.py`.
Los hashes exactos y propiedades JUnit allowlisted están en el nuevo campo
`final_after_sqlite_ownership_run` del JSON; se preservaron los resultados de
la primera repetición y las nueve celdas históricas. No se afirma que se
hayan vuelto a ejecutar los seis cruces entre proveedores después de T080.

No apareció una pausa real por cuota: ambos escenarios conservaron
`skipped/not_applicable`, con campos de observación nulos, aunque la ventana
finita configurada siguió siendo 30 segundos. La continuación por cuota sigue
sin aceptación real; no se indujo agotamiento ni se renovaron cuentas. Los
otros diez endpoints conservan los bloqueos documentados.

JUnit y logs privados de esta repetición:
`/home/felni/cao015-final-providers/final-after-sqlite/`, con directorio 0700
y archivos 0600. Solo esta evidencia sanitizada se copió a ambos workspaces.

## Repetición final tras T081–T083 — 2026-10-07

Después de las correcciones finales de cancelación/timeout del canal runtime y
compatibilidad TOML con Python 3.10, se repitieron las tres celdas reales
disponibles del mismo proveedor contra el candidato congelado de **1358
registros**: **3 passed, 0 failed, 0 skipped**, exit 0, **211.185 s**.
Duraciones: `codex->codex`: 56.873 s; `claude_code->claude_code`: 77.298 s; `opencode_cli->opencode_cli`: 76.860 s.

El SHA-256 del manifiesto final es
`a2a9b937a17ac6c8db7dc72c67f5793fa930cf07b989c72ce6cb06685dcf325d`. Nueve hashes
de fuentes enfocados coinciden entre workspace, candidato y manifiesto,
incluyendo `runtime_channel/registry.py`, el harness, fixture, tres adaptadores
y las tres capas de propiedad SQLite contrastadas en la repetición anterior.
El JSON añade `final_after_runtime_cancellation_run`; conserva íntegramente
los resultados anteriores, incluido el snapshot de 1335 registros y los
resultados históricos de las nueve combinaciones. Se volvieron a ejecutar
únicamente las tres celdas del mismo proveedor.

Cada celda acreditó turno y recibo/join, entrega persistida, cancelación con
limpieza, reconciliación después de timeout y eliminación de sesión. El runner
confirmó la eliminación de todos los hogares/perfiles y copias privadas de
autenticación de su ámbito, y detuvo los recursos tmux privados. Se mantuvieron
los modelos exactos y los registros de login revisados; no se inició login
para los otros diez endpoints bloqueados.

No hubo pausa real por cuota ni continuación observable. Ambos escenarios
permanecen `skipped/not_applicable`, con campos de observación nulos y ventana
configurada de 30 segundos. No se agotaron cuentas deliberadamente ni se
reenviaron turnos pausados.

JUnit y logs privados:
`/home/felni/cao015-final-providers/final-after-runtime-cancellation/`
(directorio 0700 y archivos 0600). Solo la evidencia allowlisted se actualizó
en ambos workspaces; no se incorporó salida de modelos ni hogares de workers.

## Disponibilidad de evidencia histórica

Los resultados y hashes registrados en este documento y su JSON se conservan. Las carpetas privadas de ejecución y sus logs fueron eliminadas al limpiar el entorno, por lo que no están disponibles para una nueva inspección. No se reejecutaron ni se ampliaron las celdas reales durante esta recuperación; la falta de cuentas de los otros diez proveedores sigue confirmada por el usuario.

La revisión independiente tras T087 verificó que los nueve hashes de fuentes enfocadas del último barrido real siguen coincidiendo. Los cambios posteriores son el linter de scripts y cuatro archivos de test. Las celdas reales llaman a `/terminals/run-step` sin workflow/spec/path/runenv y pasan por el sustrato agent_step, que no invoca ese linter. Esto conserva la evidencia histórica acotada de tres éxitos sin atribuir una ejecución real al manifiesto completo T087. No hay nueva prueba de pausa por cuota o continuidad ni nuevos proveedores acreditados.
