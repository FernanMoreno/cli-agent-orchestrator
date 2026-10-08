# Regresiones de fixtures descubiertas en CI remoto

## Fuente y fallo inicial

CI `37760286584` ejecutó la fuente `71156b2108f71e42020581dce50e14510e5c66c1` sobre ubuntu-latest. El job backend de MCP Apps terminó con **8 failed, 16569 passed, 127 skipped, 23 deselected, 52 warnings en 3657.69 s**. Estos resultados no se acreditan como aceptación; la matriz local completa previa mantiene su identidad T087.

Dos fallos de plugins atravesaron el preflight real de Kiro antes del Provider.initialize simulado: el CLI no existe en el runner. La reproducción local con PATH=/usr/bin:/bin produjo **2 FAIL/2 PASS/28 deselected** antes del cambio, sin cuenta externa; el perfil se retiró después de EOF.

Seis casos Work esperaban namespaces operativos después de comprobar sólo plataforma/binario o sin preflight. El kernel rechazó el mapeo uid_map. La reproducción con un unshare ejecutable que devuelve esa denegación produjo **6 FAIL/24 deselected/2 warnings en 2.43 s** antes del cambio. SHA-256 del log RED: `2f8cac1de182a3e1ebb31b953fa57ee439023ca16a309580462f36f34ee1a425`. El diagnóstico precede la corrección; una denegación de capacidad no constituye fallo del aislamiento de producción.

La aceptación QEMU de ese mismo commit aprobó **8 PASS en 288.27 s**, y conserva la prueba real de aislamiento/reinicio; véase [publicación del fork](final-fork-publication-evidence.md). Los resultados GREEN y la revisión se registran en las secciones siguientes.

## T088 — proveedor simulado autocontenido

La fixture sustituye únicamente el preflight Kiro dentro del ExitStack ya existente, junto al Provider.initialize simulado. Las reconciliaciones reales, el rechazo de bytes alterados, el éxito del caso autorizado, el escaneo del plugin y la comprobación de que el event loop responde conservan sus aserciones. Producción permanece sin cambios.

Con PATH=/usr/bin:/bin y ausencia comprobada de kiro-cli: GREEN **4 PASS/28 deselected**; archivo completo, proyección/procedencia y regresiones reales de capabilities con runners inyectados: **134 PASS/2 avisos Pydantic existentes**. Los tres perfiles se retiraron en el EOF de cada ejecución. La revisión independiente aprobó especificación y calidad, sin P1/P2; no ejecutó otra vez las mismas pruebas ni acredita un proveedor real autenticado.

El control nativo anterior a T089 ejecutó los seis casos sobre capacidad disponible: **6 PASS/24 deselected/2 avisos en 3.08 s**. SHA-256 del log de control: `bea55c05393c349c37144db53c700d0083aef073b3af3a748a48f31dec71ad78`. El log inicial de CI descargado tiene SHA-256 `1862b92cc22e055e57a31fdb279a881e1bc39e83b02df10e4abb274231f94640`; se retiró después del diagnóstico/revisión y de conservar este resultado.

## T089 — capacidad exacta y resultados separados

Sólo seis casos nativos usan el preflight exacto de producción antes de iniciar sus efectos. La omisión de net/IPC usa el ejecutable previamente validado y conserva las aserciones de gating/identidad. Las pruebas de rechazo y el job QEMU obligatorio mantienen sus fronteras. La omisión nueva exige un prerequisito ausente conocido o exit 1 y una operación namespace concreta (unshare failed, uid_map o gid_map) con EPERM/EACCES; errores de ejecución, otro status, timeout o diagnóstico inesperado fallan.

La revisión inicial detectó dos P2 y se reprodujeron antes de corregir: un error al ejecutar Python con Permission denied o un status incorrecto produjo **3 FAIL/5 PASS**; ausencia de plataforma, pidfd o pipe2 produjo **7 FAIL/1 PASS** en cada condición. Las regresiones inesperadas convierten un SKIP incorrecto en FAIL explícito. La fixture de prerequisitos afecta únicamente los siete casos con ejecutables dobles; no requiere namespaces operativos y no limita la regresión pura de RuntimeError.

Resultado final: módulo y tres vecinos **91 PASS/0 SKIP en 29.21 s**; denegación simulada **12 PASS/6 SKIP/20 deselected en 5.43 s**; cada prerequisito ausente **1 PASS/7 SKIP/30 deselected**. Los seis SKIP son omisiones de capacidad, no aceptación nativa. La revisión independiente posterior marcó ambos P2 ADDRESSED y aprobó especificación/calidad del fix sin nuevos P1/P2. Black, diff y la conservación de los seis consumidores/QEMU se comprobaron. Todos los perfiles y logs propios de cada ejecución se retiraron después de guardar comandos, resultados y hashes.

## Identidad posterior a T089

El [manifiesto T087](final-source-manifest-t087.json) conserva la matriz anterior. El manifiesto del commit `3276454aea81030f99cb03cf6a21e621e5a17509`, SHA-256 `6e6a9aa0abf336f1f290bac0c4cbf6019a8d9d395d1443a83d58f1704ec06f87`, conserva 1358 registros; el freeze de configuración adicional conserva 1361, SHA-256 `870d40727ed3fb604e92deecd65c81aaff8fb1a964f0e13e75c1003ee1980b98`. Sólo cambian los dos archivos de fixtures anteriores; producción y locks conservan exactamente sus bytes. La aceptación global del nuevo candidato depende del hook completo y del CI remoto posterior; los 8 FAIL anteriores continúan siendo FAIL.

## T090 — orden del shim E2E y HTTP local

El hook normal completo de T089 produjo **1 FAIL/16530 PASS/74 SKIP/93 warnings en 2419.23 s** y bloqueó el push; véase [evidencia del fork](final-fork-publication-evidence.md). `_patch_api_base_url_for_e2e` cambia `constants.API_BASE_URL` y aliases E2E hasta el teardown de sesión, mientras memory_gateway conserva su alias importado. La aserción de la prueba leía el valor global mutado. La reproducción exacta del helper, sin iniciar servidor, produjo el mismo RED **1 FAIL/3 warnings en 1.45 s**, SHA-256 `0478d9e52705de5d8a3357cb8776be5e7a7bf62458d37ea616a7236e7a9d4823`.

Sólo esa fixture cambia (+4/−2): configura `memory_gateway.API_BASE_URL` con un puerto local explícito no predeterminado y comprueba el destino literal `/internal/memory/recall`. Conserva bearer, éxito MCP, override remoto ausente y fallo si MemoryService se usa directamente. El mismo orden produce GREEN **1 PASS/3 warnings en 1.22 s**, SHA-256 `6d6a149cff1a8ea83f89468c11bc608a6d67c3f7cd684e0f288533d9878b97b5`; el teardown restablece el alias del consumidor. Los warnings son de import-rewrite por pytest.main tras importar el helper.

Constantes, memory gateway y dos módulos MCP vecinos: **175 PASS/2 warnings Pydantic existentes en 36.10 s**, SHA-256 `01fd65840d14463e844cc9d9251944d02be72a51e5191479b9e8dd76c8dc0647`. Un primer wrapper asignó CAO_HOME_DIR fuera de HOME/.aws/cli-agent-orchestrator y produjo **1 FAIL/174 PASS en 38.06 s**; se corrigió únicamente ese entorno y se repitió la misma selección. Los cuatro perfiles y sus salidas temporales se eliminaron inmediatamente después de registrar cada resultado. La revisión independiente aprobó especificación/calidad, sin findings bloqueantes; no declara hook ni CI posteriores aprobados.

La fuente T090 conserva 1358 registros, manifiesto SHA-256 `64e6443b8b0523687c1e276cf97151d7a8c0497e05afe48cb59d5f06146b2e96`; configuración ampliada 1361, SHA-256 `bd8f63604ca2d5b3b05fee6ffc014c0950f28dd532c226171b490a58d10ec8b2`. Sólo tres fixtures difieren de T087. Producción, dependencias y workflows permanecen byte-identical. La matriz histórica y el fallo del hook anterior mantienen sus identidades; la aceptación global posterior sigue pendiente.

Controles T090: `project-composition-check caos` exit 0, cinco contratos conservados/cero rotos; Black/isort/diff y enlaces Markdown exit 0; Gitleaks staged exit 0/sin secretos. Trivy real sobre árbol exportado `e98545ffe2b69b4062e15ee4603e916acda000c8`, con exports locked y configuración efectiva de CI (vuln+secret, todas las severidades, ignore-unfixed), exit 0/cero resultados SARIF en 34.983 s. SHA-256 SARIF `d13e6318126b2ae555b8afef5f51edaffe155f2ffe9ecf7f0d3af8e69843dd67`; fuente `64e6443b8b0523687c1e276cf97151d7a8c0497e05afe48cb59d5f06146b2e96`. El export temporal se retiró inmediatamente después de verificar resultado/hash; cambios posteriores sólo aclaran documentación de identidades.

## T091 — host nativo para la cobertura obligatoria

El backend remoto del commit `68b95bf5570ac4bff888f256d4e37ffe48d18baf` pasó 16579 pruebas (133 SKIP), pero el ratchet falló: 63539 de 73223 sentencias, 86.77464731027136 %, frente al 87 % exigido. MCP Apps pasó su mínimo con 90.01 %. El informe completo conserva su resultado FAIL; SHA-256 del log `b77acca958346794e28d43183c57b71efd44f21033795355846e2660b898cc4e`. El módulo supervisor perdió 223 sentencias respecto a la ejecución nativa local; un recorrido real del módulo ejecutó 221 de ellas. Esa atribución no constituye un informe de cobertura combinado ni demuestra todavía la causa AppArmor del runner.

El helper de CI conserva la selección y los argumentos originales, las cinco versiones y los mínimos 87/90. Ejecuta el probe de producción sin modificarlo; sólo puede ajustar la única clave userns de AppArmor cuando observa baseline 1, rechazo exacto EPERM/EACCES y runner Ubuntu/Linux hospedado, con usuario sin privilegios. Verifica baseline 0 y un nuevo probe real antes de pytest; exige evidencia JUnit de los seis casos obligatorios y cero omisiones/fallos en todos los casos emitidos del supervisor. Restaura el baseline observado y elimina su informe privado. Missing prerequisites, otras causas, un host no elegible y evidencia ausente son FAIL. No modifica código de producción, locks, exclusiones, floors ni aceptación QEMU.

La primera revisión reprodujo un P1: esperar sólo al líder dejaba un descendiente vivo al cancelar. Tres regresiones reales fallaron antes del cambio (cancelación, líder reaped y finalización normal). El fix drena exclusivamente su PGID/SID con TERM y escalado KILL acotados antes de restaurar política, independientemente del estado del líder. Si no puede confirmar el drenaje, falla y difiere la restauración a la eliminación de la VM; SIGKILL del wrapper tampoco puede ejecutar finally. No promete contener descendientes que creen otra sesión. La re-revisión independiente declaró P1 ADDRESSED, sin nuevos problemas Critical/Important.

Validación final local: 87 PASS, cero SKIP, 22.82 s: 44 controles del helper, 38 casos nativos reales, tres vecinos FD y dos controles HTTP. Cinco escenarios reales comprueban descendientes resistentes, orden de restauración y un grupo ajeno intacto; cada verificador aislado limpia/reap sólo sus propios procesos. Dos warnings existentes de Pydantic sobre Config de clases. En WSL el probe inicial/final pasa y la clave AppArmor está ausente: no se ejecutó sudo/sysctl real. Los perfiles, procesos e informes temporales se eliminaron al terminar cada ejecución.

Fuente helper SHA-256 `e7aa7659c2192b26a1b15c7bff6f126748c0f842e6d417766894ce13ea10e445`; pruebas `1629f7861cc163bdb4013dffdcd0f58fba1e6bebe992dd7a2ef26748d8f865ac`; workflow `d94cdcb35d8c072e29030ca7f866058680dfd16cfdff9cc03d4cfe7e1ffe3e9e`. El manifiesto T091 contenía 1360 registros, SHA-256 `a7214e973b40d28f929ce64d9fc7ebc18e8f9e99fcd2cf19f84546e32de31011`; freeze ampliado 1363, SHA-256 `1effe1f38061b3cb57b86ce5b0fff32346746e47eed16974f07520631c03860a`. Frente a T087 cambian tres fixtures y dos archivos de control de CI; el launcher de ci.yml se registra por separado en configuración.

Black, isort y diff exit 0. `project-composition-check caos` conserva cinco contratos/cero rotos en el repo registrado, y `lint-imports` directamente en el candidato confirma cinco/cero (400 archivos, 1680 dependencias). Graphify real dirigido después del fix: 139 nodos/324 aristas, verificado contra el probe y los controles actuales; caché temporal retirada inmediatamente. No representa una revisión de todo el grafo. La aceptación global continúa pendiente de revisión final, hook completo y CI remoto del nuevo SHA, incluidos ratchet y QEMU.

Trivy real T091: árbol `69832ac0127f69e173bce273c65e96038157b1ee`, fuente `a7214e973b40d28f929ce64d9fc7ebc18e8f9e99fcd2cf19f84546e32de31011`, exports locked de ambos entornos, configuración efectiva de CI, exit 0/cero resultados SARIF, 48.999 s. SHA-256 SARIF `a5be4f97fa2622d74f4eecbe610406a8a2069bb59ca8fab50901ef36de3b9221`. Cambios posteriores sólo aclaran documentación de identidad/resultados. Export, logs y SARIF temporales retirados tras verificar resultado y hashes.

## T092 — selector del comando delegado y copias de documentación

El hook completo de T091 fue bloqueado por tres aserciones del selector de comandos: 3 FAIL/16572 PASS/74 SKIP. Antes de cambiar comportamiento se reprodujeron 20 FAIL focalizados. El selector ahora identifica invocaciones ejecutables exactas dentro de jobs.test, conserva unicidad y compara flags efectivos de coverage, marcador y exclusiones; comentarios, señuelos y el otro job no pueden ocultar su ausencia. Se alineó la fila de CI en el skill canónico y sus dos copias distribuidas, con identidad exacta.

La revisión detectó dos P2: comandos adicionales tras separadores y marcador vacío. Round1 reprodujo 13 FAIL/3 positivos; el marcador quedó ADDRESSED, pero la re-revisión encontró # entre comillas dentro de una palabra ocultando un separador. Round2 reprodujo 14 FAIL/8 positivos y tres rechazos adicionales de sintaxis no soportada antes del fix. La re-revisión final declaró el último P2 ADDRESSED sin nuevos Critical/Important. Se conserva un análisis literal acotado de comillas, escapes y límites de comentario; la gramática no soportada falla explícitamente.

Resultado final: 61 PASS focalizados; módulo completo y vecinos de paridad/paquetes 259 PASS/3 SKIP en 58.80 s, sin warnings. Los tres SKIP son por zsh no disponible. sync_skills --check valida 14 skills; build_agent_plugin --check mantiene ambos paquetes 2.5.0 in-sync/loadable. Pruebas SHA-256 `f8c23c962b209fe43b0b41315dbbe7fa424cb65940f645d362e8881c51267497`; los tres documentos comparten `eaa66a32701038858e4a5e2622cd0647b89df8a519a08f02765c7a49ce011624`. Helper nativo/workflow/runtime no cambian. Perfiles y outputs temporales retirados al finalizar cada ejecución.

## T093 — IDs de parametrización Docker

Una recolección real, sin ejecutar pruebas, encontró 16814 casos y un nodeid de 8388713 bytes: pytest incluía el binario inválido de 8 MiB + 1 en el nombre del caso Docker. Recolectar salió exit 0; RED significa tamaño medido fuera del presupuesto diagnóstico, no un fallo de pytest ni prueba de la causa del timeout hospedado. Se añadieron solamente los IDs empty/mutable/oversized a los mismos tres payloads. El AST de argumentos y cuerpo/aserciones se conserva idéntico.

Después, los tres IDs miden 109–113 bytes; los tres casos verbose y sus vecinos pasan (92 PASS, 6.16 s, output 13205 bytes). La revisión independiente declara spec PASS/quality Approved, sin P1/P2. Fuente SHA-256 `841a86d74bc31c67695bde560b9e43e7080f1ae9e71ac89753641458a8811a5b`. La recolección completa posterior vuelve a registrar exactamente 16814 casos, exit 0, 1842448 bytes de IDs totales y máximo 10089 bytes. No acredita ejecución de la suite completa ni demuestra causalidad de las cancelaciones antiguas.

La fuente final T093 contiene 1363 registros, incluidos los tres documentos canónico/distribuidos, manifiesto SHA-256 `b0b5836af7ff40a0ddd18d9bf0d238961cce6827130dc3cad902a313c6a7bcaa`; freeze ampliado 1366, SHA-256 `6d58e988dc885a83c720ae19a6fd705f9d954876fe697ad95570f08313fdc174`. Código runtime, dependencias y QEMU conservan los bytes T087; cambia el launcher CI, controles de pruebas y documentación distribuida. Los artefactos wheel históricos conservan su identidad anterior; la paridad de documentos actual no atribuye esos binarios a la fuente nueva. Todos los perfiles de recolección/ejecución se eliminaron inmediatamente después de registrar resultados. Hook completo y CI del SHA nuevo siguen pendientes.

El hook completo posterior T093 confirma 16633 PASS/74 SKIP/93 warnings en 2933.23 s, JIT/tamaño aprobados y push exit 0 a `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`; ver [recibo de publicación](final-fork-publication-evidence.md). Los 1366 posthash coinciden; perfil y log eliminados. T092 queda verificado también en suite completa; T091 mantiene pendiente la aceptación hospedada/floors 87+90 y QEMU del nuevo SHA.

## Primer recibo hospedado T093: Python 3.13

Job 113450117535 del CI 37817604297 termina success sobre `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`: CPython real **3.13.16**, **16703 PASS/111 SKIP/23 deselected/1602 warnings en 2939.00 s**. El runner declara Ubuntu 24.04/kernel 6.17.0-1022-azure/UID 1001, image ubuntu24/20261004.327.1. Helper observa AppArmor userns=1 y probe real FAIL con uid_map EPERM; tras el único cambio 1→0, final_probe PASS; JUnit acredita **38 supervisor PASS/0 SKIP**, incluidos los seis requeridos. Al terminar observa restauración 0→1 verificada. Esto prueba el before/after de ese host; no atribuye el antiguo timeout a AppArmor o al nodeid.

SHA-256 del log GitHub `26df720ba55f2f37020c5999bfce02bcc1c7071dae68ee21c38e9c9a00b939c5`; descarga temporal eliminada tras extraer el recibo. Las otras cuatro versiones y el ratchet conjunto siguen pendientes.

## Ratchet hospedado T093 aprobado

Job 113450117380 del mismo SHA/CI termina success, CPython real **3.12.15**: **16703 PASS/111 SKIP/23 deselected/52 warnings en 3676.35 s**. Probe inicial uid_map EPERM con AppArmor=1; preparación observada 1→0; probe final real PASS; **38 supervisor PASS/0 SKIP**, incluidos seis requeridos; baseline 0→1 restaurado y verificado. Ratchet real conjunta: **Python 87.08% ≥ 87.00%, frontend 90.01% ≥ 90.00%**, sin cambios de mínimos, exclusiones ni selección. Se resuelve el fallo anterior 86.7746% del host restringido sin tocar política runtime. No se atribuye el timeout histórico a una causa no observada.

SHA-256 log GitHub `dfe4748f1378d085c06ad16675645f7d467353f2ff54a2960a13e8b8c21c4f8d`; descarga temporal retirada inmediatamente. Python 3.14 real 3.14.8 también termina success: **16703 PASS/111 SKIP/23 deselected/1672 warnings en 3499.98 s**, log SHA-256 `3d16dd064f7c5512b88c2e363ff05a372b7cdc488cdecd1e6a1cfc7ec5c824e5`; profile before/after y 38 nativos verificados. Matriz 3.10/3.11/3.12 aún pendiente.

Recibos de matriz del SHA T093 (descargas borradas tras cada extracción):

| CPython observado | Resumen pytest | SHA-256 log |
|---|---|---|
| 3.10.22 | 16702 passed, 112 skipped, 23 deselected, 23 warnings in 4171.21s (1:09:31) | `9c42c9780aa15175ae8e43c18935889f14ee0b585df177976f3a661e9b61c265` |
| 3.11.17 | 16702 passed, 112 skipped, 23 deselected, 23 warnings in 4356.02s (1:12:36) | `c2cfe018e8a83b290137d2fae311474e26e5442d8f57a29cc59fc5696fadadb5` |
| 3.13.16 | 16703 passed, 111 skipped, 23 deselected, 1602 warnings in 2939.00s (0:48:58) | `26df720ba55f2f37020c5999bfce02bcc1c7071dae68ee21c38e9c9a00b939c5` |
| 3.14.8 | 16703 passed, 111 skipped, 23 deselected, 1672 warnings in 3499.98s (0:58:19) | `3d16dd064f7c5512b88c2e363ff05a372b7cdc488cdecd1e6a1cfc7ec5c824e5` |

Cada job observa probe inicial denegado, preparación, probe final real aprobado, 38 supervisor PASS/0 SKIP y restauración verificada. El job Unit Tests (3.12) continúa pendiente; la versión se verificará en su log, sin inferirla sólo por la etiqueta del job.

El primer Unit Tests (3.12) del CI T093 continuaba activo después de más de dos horas, mientras los otros cuatro terminaron en 49–73 minutos. Una consulta del log activo obtuvo HTTP 404; su output temporal se eliminó. Se solicitó cancelar el intento y reintentar únicamente el trabajo incompleto sobre el mismo SHA, sin cambios de código/selección/mínimos. Esto es recuperación diagnóstica; el intento interrumpido no se acredita como aprobado y la causa sigue sin prueba.

Después de la cancelación, el log del primer 3.12 sí estuvo disponible (2340223 bytes, SHA-256 `f6edad970f905662fd6835594a6954e081bacc14d497f923538800b09017f4b7`). Sus últimas pruebas pasan al **89% hasta 19:37:18Z**: el trabajo seguía avanzando, por lo que **no se acredita un bloqueo**. El motivo de su lentitud no está demostrado. La cancelación diagnóstica interrumpió una ejecución todavía parcial; no hubo resumen completo ni aprobación de ese job. Descarga eliminada inmediatamente. Se solicitó rerun sólo de Unit Tests (3.12) y sus dependientes sobre el mismo commit.

## Aceptación hospedada final T093

[CI 37817604297, attempt 2](https://github.com/FernanMoreno/cli-agent-orchestrator/actions/runs/37817604297) concluye **success** sobre `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0`: **19 trabajos success**, Required release gates incluido; Dependency Review es el único SKIP, condicionado al evento push. El reintento únicamente de 3.12 conserva los resultados previos (sus nuevos IDs mantienen los timestamps originales, verificado por API). Unit Tests (3.12), job 113504196866: CPython observado **3.12.15**, **16703 PASS/111 SKIP/23 deselected/52 warnings en 3906.07 s**; probe antes denegado, preparación observada, final real PASS, **38 supervisor PASS/0 SKIP**, y restauración 0→1 verificada. Log SHA-256 `8cb1de5bfa0278a275ecb13e9179df42f8153ba0d3a4d2072c00439e7e19cbe0`; descarga temporal eliminada inmediatamente.

Las cinco versiones reales 3.10.22/3.11.17/3.12.15/3.13.16/3.14.8 suman **83513 PASS/557 SKIP, cero fallos/errores** en sus selecciones completas (23 casos deselected por job). Ratchet obligatorio **87.08/90.01**, QEMU estricto **8 PASS**, cargo-deny, arquitectura, calidad, seguridad, Web y MCP/Playwright aprobados. Se cierra T091 sin bajar mínimos ni cambiar producto/selección. El primer 3.12 cancelado y los CI históricos fallidos conservan sus resultados; no se atribuye su duración a una causa no probada. Los estados pendientes de secciones anteriores son recibos históricos. El commit documental de cierre conserva los mismos 1366 registros/hashes del freeze y no cambia runtime, tests, configuración ni locks.
