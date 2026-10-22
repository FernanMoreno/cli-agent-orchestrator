# Centralización de worktrees en main · 2026-10-22

Base revisada: `29c4100bb75f8b6b1f09ab855dfaa4316f868d6b`. [Inventario verificable](consolidation-20261022.json). El código funcional más reciente ya estaba incorporado en main; las diferencias restantes son genealogía Git, checkpoints antiguos y artefactos de trabajo. Esta consolidación conserva la implementación vigente y reúne la historia pendiente del frontend después de verificar su equivalencia.

## Inventario y decisiones

Hay 16 registros de worktree: 7 directorios presentes contando main y 9 directorios ausentes. Todos los HEAD registrados son antecesores de main salvo los 10 commits de la rama frontend. Los directorios ausentes no contienen un delta local accesible; sus commits registrados sí están incluidos. No se han borrado registros ni reparado referencias.

| Árbol | Comprobación | Resultado en main |
| --- | --- | --- |
| Principal | main 29c4100b; doce archivos Graphify ajenos preservados | Fuente autoritativa |
| Snapshot de publicación | d0dd2e3a, limpio y antecesor | Sin código exclusivo |
| Work replacement, 1cb8 | 13 modificados: diez de implementación/pruebas idénticos y tres checkpoints documentales anteriores; cinco sin seguimiento: tres fuentes/pruebas idénticos y dos validaciones anteriores | Ya incorporado; sus checkpoints documentales son anteriores |
| Work dispatcher, 35d7 | Helpers/pruebas idénticos o versiones anteriores a los fixes T066/T067/T068 | Conservar admisión, cancelación, locks y metadatos actuales; no superponer archivos antiguos |
| Frontend/graph | Funcionalidad integrada previamente en 4678ae72; cinco deltas locales: cuatro fuentes/pruebas idénticos y un timestamp Graphify; nueve artefactos de maqueta sin seguimiento ya presentes en main | Reunir los 10 commits aecafa83 sin cambiar el contenido vigente |
| T097 | 221 estados/rutas y 221 hashes coinciden con el manifiesto histórico; escenarios contrastados con la fuente actual | Sin delta exclusivo de producto; la frontera SQLite cambió posteriormente |
| Candidate antiguo | Referencia Git rota; 2202 archivos de texto seguros coinciden con 02309f3f, ya antecesor | Archivo histórico conservado; no se certifica su estado Git completo |

La comparación del candidate excluye 171 rutas de VCS, caché, evidencia, binarias o sensibles; sus 29 extras identificados son generados. Esta limitación no se presenta como una revisión completa de archivos ni autoriza su eliminación.

El mapa de ejecución, navegación Studio, pestañas accesibles y revocación de bearer del frontend ya estaban en main. Los cuatro archivos modificados del worktree contienen las correcciones posteriores que main también conserva. Sus versiones confirmadas eran anteriores; traerlas encima habría retirado mejoras. La unión de genealogía usa la estrategia `ours` tras la comparación de contenido: no implica elegir una versión menos completa. El HTML, capturas y controles del grafo de la maqueta aceptada también están en main; su evidencia e investigación incluyen las verificaciones adicionales de Codex.

## Recuperación y compatibilidad histórica

La consolidación del 12 de octubre describía importadores y catálogos SQLite que existían entonces. La fuente actual exige su retirada en [test_postgres_only_source_guard.py](../../test/clients/test_postgres_only_source_guard.py); esos destinos ya no constituyen cobertura vigente. La recuperación actual es PostgreSQL y tiene sus pruebas en [test_postgres_recovery_bundle.py](../../test/services/test_postgres_recovery_bundle.py), [test_recovery_bundle.py](../../test/services/test_recovery_bundle.py) y [test_postgres_import_recovery_composition.py](../../test/services/test_postgres_import_recovery_composition.py).

No se declara equivalencia de una importación SQLite que ya no está soportada. Tampoco se restauran motores retirados para hacer coincidir una copia antigua. Los tests actuales del dispatcher incluyen concurrencia, backoff, cancelación y shutdown posteriores al manifiesto T097; el test de efectos del proxy conserva el contenido portado. Las pruebas host/guest y T074 siguen siendo evidencias separadas.

## Escritorio y conservación

`CAOS` y `caos` son el mismo directorio. Las dos demos del Escritorio son aplicaciones de ejemplo, con servidor/frontend/contrato iguales a sus copias en `examples/agent-collaboration/tasks/` y `turn-recovery/`. La carpeta de verificación contiene cinco logs históricos. No son otras versiones del producto ni mejoras pendientes de importar.

Se conservan los worktrees originales, sus cambios locales y los doce artefactos Graphify ajenos. Los patches y manifiestos de origen quedan en un directorio privado del operador. No se copian credenciales, datos mutables, cachés ni logs privados al producto. No se ha alterado el estado de ningún job histórico.

## Validación y revisión de composición

Se verificaron 1812 archivos mantenidos de código/pruebas: ningún byte funcional cambia respecto a la base. Los doce archivos Graphify ajenos y los hashes/HEAD/deltas de origen registrados permanecen intactos. El merge reúne la historia del frontend con dos padres, conservando el contenido funcional previamente validado de main.

- Frontend: 48 módulos y 531 pruebas pasan; TypeScript y build de producción terminan con código 0.
- Backend: 77 pruebas pasan, con 15 avisos y sin fallos; incluye integración PostgreSQL de admisión y orden de locks, migración de origen, dispatcher, reemplazo, bloqueo, cancelación y guardas PostgreSQL-only. Se mantuvo la recogida de cobertura configurada; esta selección no acredita cobertura global.
- Arquitectura: `project-composition-check "$(cat .ai/project-name)"` pasa: 412 archivos, 1834 dependencias, cinco contratos conservados y ninguno roto.

La revisión cubre las fronteras frontend/autenticación y Work/admisión/scheduler/persistencia: identidad y revocación, reemplazo, cancelación, orden de locks y ciclo de vida del dispatcher. Se reutilizó el contenedor PostgreSQL existente verificado, sin reinstalaciones. Los contratos API/modelo no cambian en esta consolidación; no corresponde añadir un contrato externo nuevo. No hubo fallos en las comprobaciones ejecutadas. La revisión independiente de Claude fue solo de lectura: revisó la precedencia del frontend sobre la comparación byte a byte del líder y justificó conservar las correcciones posteriores de main. La revisión final aprobó el alcance con cuatro aclaraciones documentales, incorporadas aquí: enlace al inventario actual, desglose de conteos de frontend y Work 1cb8, y atribución precisa de la revisión.

Gitleaks revisó los diez commits del frontend que pasan a ser alcanzables desde main: no detectó secretos.

Veredicto de composición: PASS dentro de este alcance. Permanecen separadas la aceptación global de modelos/perfiles/concurrencia, los recorridos nativos y T074 host/guest; no se ejecutó QEMU ni se alteraron jobs históricos.

Cierre Git: merge `fddfbf85bf640877a2b81188ba5258f7a4ddcb2a` en main con los dos padres esperados. Tras el commit se revalidaron los 1812 hashes funcionales y los doce artefactos ajenos; la rama frontend queda incluida por ascendencia. El hook normal de pre-commit terminó sin errores y el escaneo de los seis documentos preparados no detectó secretos. T076–T078 quedan cerradas para esta centralización.
