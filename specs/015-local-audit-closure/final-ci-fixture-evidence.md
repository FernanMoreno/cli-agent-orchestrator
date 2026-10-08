# Regresiones de fixtures descubiertas en CI remoto

## Fuente y fallo inicial

CI `37760286584` ejecutó la fuente `71156b2108f71e42020581dce50e14510e5c66c1` sobre ubuntu-latest. El job backend de MCP Apps terminó con **8 failed, 16569 passed, 127 skipped, 23 deselected, 52 warnings en 3657.69 s**. Estos resultados no se acreditan como aceptación; la matriz local completa previa mantiene su identidad T087.

Dos fallos de plugins atravesaron el preflight real de Kiro antes del Provider.initialize simulado: el CLI no existe en el runner. La reproducción local con PATH=/usr/bin:/bin produjo **2 FAIL/2 PASS/28 deselected** antes del cambio, sin cuenta externa; el perfil se retiró después de EOF.

Seis casos Work esperaban namespaces operativos después de comprobar sólo plataforma/binario o sin preflight. El kernel rechazó el mapeo uid_map. La reproducción con un unshare ejecutable que devuelve esa denegación produjo **6 FAIL/24 deselected/2 warnings en 2.43 s** antes del cambio. SHA-256 del log RED: `2f8cac1de182a3e1ebb31b953fa57ee439023ca16a309580462f36f34ee1a425`. El diagnóstico precede la corrección; una denegación de capacidad no constituye fallo del aislamiento de producción.

La aceptación QEMU de ese mismo commit aprobó **8 PASS en 288.27 s**, y conserva la prueba real de aislamiento/reinicio; véase [publicación del fork](final-fork-publication-evidence.md). Los resultados GREEN y la revisión se incorporarán tras ejecutarse.

## T088 — proveedor simulado autocontenido

La fixture sustituye únicamente el preflight Kiro dentro del ExitStack ya existente, junto al Provider.initialize simulado. Las reconciliaciones reales, el rechazo de bytes alterados, el éxito del caso autorizado, el escaneo del plugin y la comprobación de que el event loop responde conservan sus aserciones. Producción permanece sin cambios.

Con PATH=/usr/bin:/bin y ausencia comprobada de kiro-cli: GREEN **4 PASS/28 deselected**; archivo completo, proyección/procedencia y regresiones reales de capabilities con runners inyectados: **134 PASS/2 avisos Pydantic existentes**. Los tres perfiles se retiraron en el EOF de cada ejecución. La revisión independiente aprobó especificación y calidad, sin P1/P2; no ejecutó otra vez las mismas pruebas ni acredita un proveedor real autenticado.

El control nativo anterior a T089 ejecutó los seis casos sobre capacidad disponible: **6 PASS/24 deselected/2 avisos en 3.08 s**. SHA-256 del log de control: `bea55c05393c349c37144db53c700d0083aef073b3af3a748a48f31dec71ad78`. El log inicial de CI descargado tiene SHA-256 `1862b92cc22e055e57a31fdb279a881e1bc39e83b02df10e4abb274231f94640`; se retiró después del diagnóstico/revisión y de conservar este resultado.

## T089 — capacidad exacta y resultados separados

Sólo seis casos nativos usan el preflight exacto de producción antes de iniciar sus efectos. La omisión de net/IPC usa el ejecutable previamente validado y conserva las aserciones de gating/identidad. Las pruebas de rechazo y el job QEMU obligatorio mantienen sus fronteras. La omisión nueva exige un prerequisito ausente conocido o exit 1 y una operación namespace concreta (unshare failed, uid_map o gid_map) con EPERM/EACCES; errores de ejecución, otro status, timeout o diagnóstico inesperado fallan.

La revisión inicial detectó dos P2 y se reprodujeron antes de corregir: un error al ejecutar Python con Permission denied o un status incorrecto produjo **3 FAIL/5 PASS**; ausencia de plataforma, pidfd o pipe2 produjo **7 FAIL/1 PASS** en cada condición. Las regresiones inesperadas convierten un SKIP incorrecto en FAIL explícito. La fixture de prerequisitos afecta únicamente los siete casos con ejecutables dobles; no requiere namespaces operativos y no limita la regresión pura de RuntimeError.

Resultado final: módulo y tres vecinos **91 PASS/0 SKIP en 29.21 s**; denegación simulada **12 PASS/6 SKIP/20 deselected en 5.43 s**; cada prerequisito ausente **1 PASS/7 SKIP/30 deselected**. Los seis SKIP son omisiones de capacidad, no aceptación nativa. La revisión independiente posterior marcó ambos P2 ADDRESSED y aprobó especificación/calidad del fix sin nuevos P1/P2. Black, diff y la conservación de los seis consumidores/QEMU se comprobaron. Todos los perfiles y logs propios de cada ejecución se retiraron después de guardar comandos, resultados y hashes.

## Identidad posterior al fix

El [manifiesto T087](final-source-manifest-t087.json) conserva la matriz anterior. El [manifiesto actual](final-source-manifest.json), SHA-256 `6e6a9aa0abf336f1f290bac0c4cbf6019a8d9d395d1443a83d58f1704ec06f87`, conserva 1358 registros; el freeze de configuración adicional conserva 1361, SHA-256 `870d40727ed3fb604e92deecd65c81aaff8fb1a964f0e13e75c1003ee1980b98`. Sólo cambian los dos archivos de fixtures anteriores; producción y locks conservan exactamente sus bytes. La aceptación global del nuevo candidato depende del hook completo y del CI remoto posterior; los 8 FAIL anteriores continúan siendo FAIL.
