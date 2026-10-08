# Verificación de publicación del fork

## Fuente y validación local

Commit de implementación: `4f88b7fd47575af0dff07b2d3c04585660eee501`, basado en `87e2d5f354374ea8acc685275e749992df51cdbd`. El commit se creó con el hook normal de precommit, sin bypass; Prettier comprobó 17 archivos y lint-staged terminó correctamente. El análisis real Gitleaks del rango base–commit examinó un commit, 6.46 MB y no encontró secretos.

La [matriz Python completa](final-python-matrix-evidence.md) aprueba compatibilidad en las cinco versiones y mantiene separado el diagnóstico de cobertura de Python 3.14. El ratchet obligatorio de Python 3.12/MCP Apps y los demás controles locales están en [integración final](final-integration-evidence.md).

## Primer intento de push

El push normal SSH ejecutó el hook completo: **16523 passed, 74 skipped, 81 warnings en 4225.65 s**, seguido del análisis JIT real de cuatro bundles limpio y cuatro tamaños gzip aprobados (49.4/49.4/48.5/86.6 KB dentro de límites 250/250/150/120 KB). No se omitió el hook ni se sustituyó ningún runner. SHA-256 del log: `504a4e0fcde98a7ad1c47939a7f5b2e8b5ce86757a32c082b52e47c63cc7a25b`.

El transporte SSH registró `Connection to github.com closed by remote host` durante el 9% del hook; al finalizar los controles el push salió con **141 (SIGPIPE)**. La comprobación remota posterior confirmó que `origin/main` seguía en `87e2d5f354374ea8acc685275e749992df51cdbd`: **este intento no publicó el commit**. Los controles locales aprobados no convierten ese fallo de transporte en un push aprobado.

La sesión GitHub vigente corresponde a `FernanMoreno`. La lectura autenticada por HTTPS confirmó el mismo SHA del fork. El siguiente intento usará HTTPS con el credential helper oficial de gh, conservando el remote origin SSH y el hook normal; el cambio de transporte evita mantener una conexión SSH abierta durante la suite larga. No se introducen tokens en comandos ni artefactos.

## Estado de cierre

La publicación, el SHA final remoto y los resultados reales de CI permanecen pendientes. T073 no está acreditada todavía. Los perfiles temporales se retiran sólo después del EOF de sus procesos y de conservar los resultados; las regresiones mantenidas no se eliminan.

## Segundo intento HTTPS interrumpido por recursos

La prueba del hook volvió a ejecutar la selección normal con perfiles privados y datos temporales en ext4. Al 58%, dos lecturas consecutivas de memoria disponible descendieron a 502 y 458 MiB, el swap estaba lleno y las herramientas tardaban hasta 20.5 s. Se verificó la cadena de procesos del propio pytest y su TMPDIR antes de enviar SIGINT únicamente a ese master; ningún proceso ni temporal de otro proyecto se modificó.

Resultado parcial: **9667 passed, 66 skipped, 49 warnings en 2501.61 s**, pytest interrumpido y push exit 1. Este intento **no constituye aprobación**, no llegó a los controles posteriores y no publicó cambios: el fork seguía en el SHA base. SHA-256 del log: `5677f10dc6d4d14406530b33641bd0fb6098c3d6265dd7bd52d84bc476f6d735`. El posthash verifica 1361 registros de fuente/configuración sin diferencias.

El perfil temporal de este intento se retira tras confirmar EOF y registrar esta evidencia. Para reducir el consumo, el siguiente intento se prepara con un solo worker real, conservando la misma selección, el aislamiento antes de importar y el hook normal. El inicio depende de observar memoria disponible suficiente; no se omiten pruebas ni se cambia el producto para resolver una limitación externa de RAM.
