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
