# Verificación de publicación del fork

## Fuente y validación local

Commit de implementación: `4f88b7fd47575af0dff07b2d3c04585660eee501`, basado en `87e2d5f354374ea8acc685275e749992df51cdbd`. El commit se creó con el hook normal de precommit, sin bypass; Prettier comprobó 17 archivos y lint-staged terminó correctamente. El análisis real Gitleaks del rango base–commit examinó un commit, 6.46 MB y no encontró secretos.

La [matriz Python completa](final-python-matrix-evidence.md) aprueba compatibilidad en las cinco versiones y mantiene separado el diagnóstico de cobertura de Python 3.14. El ratchet obligatorio de Python 3.12/MCP Apps y los demás controles locales están en [integración final](final-integration-evidence.md).

## Primer intento de push

El push normal SSH ejecutó el hook completo: **16523 passed, 74 skipped, 81 warnings en 4225.65 s**, seguido del análisis JIT real de cuatro bundles limpio y cuatro tamaños gzip aprobados (49.4/49.4/48.5/86.6 KB dentro de límites 250/250/150/120 KB). No se omitió el hook ni se sustituyó ningún runner. SHA-256 del log: `504a4e0fcde98a7ad1c47939a7f5b2e8b5ce86757a32c082b52e47c63cc7a25b`.

El transporte SSH registró `Connection to github.com closed by remote host` durante el 9% del hook; al finalizar los controles el push salió con **141 (SIGPIPE)**. La comprobación remota posterior confirmó que `origin/main` seguía en `87e2d5f354374ea8acc685275e749992df51cdbd`: **este intento no publicó el commit**. Los controles locales aprobados no convierten ese fallo de transporte en un push aprobado.

La sesión GitHub vigente corresponde a `FernanMoreno`. La lectura autenticada por HTTPS confirmó el mismo SHA del fork. El siguiente intento usará HTTPS con el credential helper oficial de gh, conservando el remote origin SSH y el hook normal; el cambio de transporte evita mantener una conexión SSH abierta durante la suite larga. No se introducen tokens en comandos ni artefactos.

## Estado anterior al push aprobado

En ese momento, la publicación, el SHA final remoto y los resultados reales de CI permanecían pendientes. T073 no está acreditada todavía. Los perfiles temporales se retiran sólo después del EOF de sus procesos y de conservar los resultados; las regresiones mantenidas no se eliminan.

## Segundo intento HTTPS interrumpido por recursos

La prueba del hook volvió a ejecutar la selección normal con perfiles privados y datos temporales en ext4. Al 58%, dos lecturas consecutivas de memoria disponible descendieron a 502 y 458 MiB, el swap estaba lleno y las herramientas tardaban hasta 20.5 s. Se verificó la cadena de procesos del propio pytest y su TMPDIR antes de enviar SIGINT únicamente a ese master; ningún proceso ni temporal de otro proyecto se modificó.

Resultado parcial: **9667 passed, 66 skipped, 49 warnings en 2501.61 s**, pytest interrumpido y push exit 1. Este intento **no constituye aprobación**, no llegó a los controles posteriores y no publicó cambios: el fork seguía en el SHA base. SHA-256 del log: `5677f10dc6d4d14406530b33641bd0fb6098c3d6265dd7bd52d84bc476f6d735`. El posthash verifica 1361 registros de fuente/configuración sin diferencias.

El perfil temporal de este intento se retira tras confirmar EOF y registrar esta evidencia. Para reducir el consumo, el siguiente intento se prepara con un solo worker real, conservando la misma selección, el aislamiento antes de importar y el hook normal. El inicio depende de observar memoria disponible suficiente; no se omiten pruebas ni se cambia el producto para resolver una limitación externa de RAM.

## Interrupciones posteriores del entorno

El intento HTTPS con un solo worker terminó con exit 143 al 32 %, sin resumen final de pytest; la causa de la señal no quedó establecida y el monitor no envió señales. SHA-256 del log: `768dededacf0a4adbb89bb97c6b0b121deda147dd5ff5dea73f89aba21cf8eca`. Un intento independiente posterior quedó sin procesos ni exit final tras reiniciarse el entorno a las 10:35:22 del 8 de octubre (Europe/Madrid); su log terminaba al 0 % a las 05:11:21, SHA-256 `d3213528b55c35cc74f756bc68f5454dc3a71203f4545e2c618dd1d25d4dc091`. Ninguno acredita aceptación ni publicación; los perfiles inactivos se eliminaron después de conservar los resultados.

## Push HTTPS aprobado

El push no forzado terminó con **exit 0**. El hook normal completo registró **16523 passed, 74 skipped, 81 warnings en 3966.67 s**, JIT limpio en cuatro archivos y los cuatro presupuestos gzip aprobados (49.4/49.4/48.5/86.6 KB). SHA-256 del log: `eb36e88efdd3e0e22363947be9a49f8009cf59dce5fe6956e535050d06c668af`. El posthash conservó los 1361 registros sin diferencias. No se omitieron hooks, casos ni controles.

La lectura independiente de `refs/heads/main` confirmó `71156b2108f71e42020581dce50e14510e5c66c1` en `FernanMoreno/cli-agent-orchestrator`. El perfil temporal se eliminó inmediatamente tras conservar el resultado y comprobar cero procesos propios activos. CI remoto se está comprobando sobre ese SHA; un estado en ejecución no se acredita como aprobado.

La aceptación remota [T097 en QEMU](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37760286549) terminó con success sobre ese mismo SHA: **8 passed en 288.27 s**, sin omisiones en el resumen de pytest. SHA-256 del log de GitHub: `d82675af76477545a76b4bdad25d9f811e4a094114a14cb4f37625ec5e66c1e9`. El archivo temporal descargado se eliminó inmediatamente después de extraer el resultado; el log remoto conserva la evidencia original.

## Hook del candidato T088/T089: fallo de fixture de memoria

El push normal HTTPS del commit `3276454aea81030f99cb03cf6a21e621e5a17509` terminó con exit 1: **1 failed, 16530 passed, 74 skipped, 93 warnings en 2419.23 s**. El fallo fue `test/services/test_memory_gateway.py::test_local_mcp_memory_also_crosses_authenticated_http`: la aserción leía una URL del módulo constants cambiada por el shim de la fixture E2E con puerto 46901, mientras el cliente conservaba la URL importada con puerto 9889. La reproducción determinista del shim confirmó el mismo fallo; este resultado no acredita aceptación. SHA-256 del log: `ec2a8f7a1b47392795ff36f0790626ddc3f06d84c4f55e59df27566e45cb3f2c`.

El posthash verificó los 1361 registros sin diferencias. Tras confirmar cero procesos del perfil, se eliminó inmediatamente el perfil temporal; el fork conserva `main` en `71156b2108f71e42020581dce50e14510e5c66c1`. Los controles JIT/tamaño no se alcanzaron en este intento.

## Push T090 aprobado

El push normal no forzado terminó con **exit 0** y publicó `3276454a` y `68b95bf5` sobre el historial previo. La lectura independiente de `refs/heads/main` confirmó `68b95bf5570ac4bff888f256d4e37ffe48d18baf`. El hook completo produjo **16531 PASS/74 SKIP/93 warnings en 2358.81 s**, cuatro archivos JIT limpios y presupuestos gzip 49.4/49.4/48.5/86.6 KB aprobados. SHA-256 del log: `f8c35e1670b84873e26f18fe2dcde2cefc5bae99de405dbd0458662f2f9ea510`.

El posthash verificó 1361 registros sin diferencias, fuente T090 `64e6443b8b0523687c1e276cf97151d7a8c0497e05afe48cb59d5f06146b2e96`. El perfil y log temporales se eliminaron después de guardar estos resultados y confirmar cero procesos propios activos. CI `37782253145` y QEMU `37782253089` están pendientes sobre ese SHA; cargo-deny `37782253213` ya terminó con success. No se presenta ninguna ejecución pendiente como aprobada.

QEMU `37782253089` terminó con **success** sobre `68b95bf5570ac4bff888f256d4e37ffe48d18baf`: **8 PASS en 279.75 s**, sin skips/xfails/xpasses en el resumen estricto. SHA-256 del log descargado: `d9f56dfb0c77b6e32c10c953d6ec7001dd0421a657449c1bb3cad18865b0b68c`. La descarga temporal se eliminó inmediatamente después de registrar el resultado.

El job MCP Apps `113328009721` de CI `37782253145` terminó con **failure**: la selección completa pasó **16579 PASS/133 SKIP/23 deselected/52 warnings en 3885.90 s**, pero el ratchet real rechazó Python **86.77 % <87.00 %** (63539/73223 sentencias); frontend **90.01 % ≥90.00 %** aprobó. SHA-256 del log `b77acca958346794e28d43183c57b71efd44f21033795355846e2660b898cc4e`. Se conserva el fallo; la causa de la cobertura y la configuración de capacidades del host se investigan antes de cualquier cambio. Los cinco jobs Python siguen pendientes.

T091: el push normal del commit `2a22fbcf5af4b12fbe065b718b1cc80cd9e5554a` fue bloqueado por el hook: 3 FAIL/16572 PASS/74 SKIP/93 warnings en 2437.86 s, exit 1. Los tres fallos pertenecen al selector del comando en test_cao_contributing_skill_accuracy.py: no reconoce el launcher nuevo antes de comprobar coverage/marcador/exclusiones. JIT y size no se alcanzaron; no se acredita publicación. SHA-256 log `6913ece4e8e0277e23776451233dc8ce5d82e6d695d2edf99cc8da811d989f9c`; 1363 hashes posteriores idénticos, cero procesos propios vivos. Perfil y log temporales retirados tras registrar el resultado. T092 corrige ese verificador antes de un nuevo hook completo.

CI anterior `37760286584` (`71156b21`) terminó FAIL: cinco Unit Tests cancelados al alcanzar el límite de ejecución; Required release gates FAIL y backend FAIL previamente registrado. El job 3.12 terminó a las 16:04:37 UTC. Su endpoint de logs devuelve 404 BlobNotFound; el ZIP oficial contiene 31 miembros y ningún log de Unit Tests (SHA-256 `5dade91d803e38fe808a29ce40a83c8dbc8037d43e1d9dfe19e8bcb4c1912bd5`). No se infiere la prueba bloqueada ni su causa sin logs; las cinco celdas no acreditan aceptación. Archivo temporal retirado tras inspección. El CI posterior del SHA nuevo continúa siendo obligatorio.

## Publicación T091–T093: hook completo aprobado

El push normal HTTPS publicó los commits `2a22fbcf5af4b12fbe065b718b1cc80cd9e5554a` y `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0` a main del fork, sin force ni omisión de hooks. El hook completo registra **16633 passed, 74 skipped, 93 warnings en 2933.23 s**; JIT limpio en cuatro archivos y gzip dashboard/agent/event-stream/graph 49.4/49.4/48.5/86.6 KB, dentro de presupuesto. Push exit 0 y lectura independiente de refs/heads/main confirma `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`. SHA-256 del log: `aa7110ecf1ac51bb91b1dfa079851a1e3d78f9e33465169ded9d72f887de0ba9`.

Los 1366 registros posteriores coinciden sin cambios; cero procesos del perfil propio. Perfil y log temporal fueron eliminados inmediatamente después de extraer la evidencia. La copia original se actualiza avanzando main, conservando cambios ajenos de Graphify/auditoría. [CI del SHA publicado](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37817604297) y [QEMU estricto](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37817604237) siguen pendientes; este recibo acredita publicación y hook, no aceptación hospedada.

La aceptación estricta [QEMU T097 del nuevo SHA](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37817604237) terminó success: **8 passed en 276.14 s**, sin omisiones; commit `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`. SHA-256 del log GitHub `3df9df431b91af156cab344d5b73455806073c386d10b6f020ab0a5bcd4e994e`; descarga temporal eliminada inmediatamente tras extraer la evidencia. cargo-deny del mismo SHA también terminó success. Matriz CI/cobertura aún en ejecución.

## Cierre de CI del SHA publicado

[CI 37817604297 attempt 2](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37817604297) termina **success** sobre `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`, 19 trabajos aprobados incluido Required release gates; sólo Dependency Review omitido por condición de push. Las cinco versiones CPython observadas aprueban sus selecciones completas: **83513 PASS/557 SKIP/0 FAIL**, además de 23 deselected por job. Python 3.12 reintentado concluye **16703 PASS/111 SKIP/52 warnings, 3906.07 s**; su primer intento fue cancelado por recuperación diagnóstica y el log mostró progreso al 89%, sin bloqueo probado. No se transforma ese intento parcial en aprobación. [Recibos/hashes por job](final-ci-fixture-evidence.md) y limpieza inmediata de cada descarga conservados. Ratchet 87.08/90.01, QEMU8PASS276.14s y cargo-deny success.

T073/T091–T093 quedan verificados. El siguiente commit sólo añade estos recibos/estados; conserva fuente congelada, código, pruebas, CI y dependencias. Se publica mediante commit/push normal con los hooks íntegros. No se crea release ni publicación de paquetes. Los diez proveedores sin cuentas y la rama de cuota no observada siguen como límites explícitos en su evidencia; no se acreditan como pruebas reales aprobadas.

Resultado final del CI histórico `37782253145` sobre `68b95bf5`: **failure**, backend ratchet fallido 86.7746% y cinco Unit Tests cancelados al límite de ejecución; Required release gates también failure. No se considera aprobado ni se sustituye su resultado por el CI T093 posterior.
