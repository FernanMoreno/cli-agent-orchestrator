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
