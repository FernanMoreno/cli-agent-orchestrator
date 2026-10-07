# Convergencia local — controles y correcciones del padre


**Estado actual: convergencia local aprobada.** Las 68 tareas están completadas. Selección completa de CI en Python **3.12.13**: **16470 passed, 110 skipped, 83 warnings, cero fallos, exit 0**. Cobertura Python **87.0883965%** y frontend **90.64%**; ratchet aprobado con ambos informes presentes y baseline idéntico. Mypy, formato, composición, clientes, navegador y Rust pasan sus controles indicados. Los diagnósticos con fallos y estados pendientes siguientes son historial; el apartado «Cierre local verificado» fija la evidencia final.

El usuario amplió el alcance: «corrige todo y no te detengas». Se conserva el árbol previo y no se ejecutan commits ni operaciones remotas.

## Método y configuración efectiva

El diagnóstico inicial reprodujo 531 errores en 81 archivos (`mypy-after-review.log`). Se añadieron T057–T064 al inventario de tareas; las comprobaciones focalizadas y sus límites están en los informes types-data, types-work, types-api, types-memory y types-runtime.

El control obligatorio es `uv run --no-sync mypy src/`. La configuración efectiva es `mypy.ini`, que tiene precedencia sobre la sección de `pyproject.toml`. No se añadieron supresiones ni se cambiaron reglas. Aprobar ese comando no equivale a comprobar todos los cuerpos sin anotaciones ni a habilitar un modo estricto adicional.

El padre corrigió inferencias de kwargs heterogéneos con un TypedDict, anotaciones de campos opcionales y callbacks, la conservación de tipos en wrappers de tmux y defaults equivalentes de Pydantic con `Field(default=None)`. Se contrastaron los casts de documentos internos con la forma garantizada por sus productores. El cleanup MiniMax conserva su resultado histórico `None` de forma explícita.

## Desaparición de proyecciones remotas

Se reprodujeron tres fallos por ausencia de proyecciones después de leer inventarios: sesión con terminal desaparecido, consulta de estado y contadores de turnos. RED: tres fallos. Las consultas de estado devuelven UNKNOWN y `(0, 0)` al desaparecer la proyección; una sesión conserva sus terminales vivos. GREEN ampliado: 203 passed, 3 deselected, exit 0.

La revisión independiente añadió get_terminal/get_turn y la enumeración de sesiones: el inventario y la proyección no son una transacción conjunta. RED ampliado: cinco fallos y tres aciertos. GREEN: 60 passed. Las lecturas individuales devuelven el error normal not-found y las enumeraciones omiten proyecciones desaparecidas. No se simula existencia mediante un cast o assert.

Regresiones adicionales MCP/perfiles/configuración/worktrees/markers: 227 passed, exit 0. Aceptación real entre dos procesos CAO tras el cambio de modelos: 1 passed en 54.92 s, exit 0, con SQLite y trabajadores mock_cli reales y sin credenciales de proveedores.

## Navegador

Chromium no arrancaba por `libasound.so.2`. Se descargó el paquete oficial Ubuntu `libasound2t64`, se extrajo en un directorio temporal propio y se añadió su directorio de bibliotecas a `LD_LIBRARY_PATH` del proceso. No se instaló en el sistema ni se omitieron aserciones. El navegador existente se seleccionó en un config temporal con servidor y puerto aislados.

Event-stream focalizado: 1 passed. Suite E2E completa MCP Apps: 7 passed en 27.0 s, exit 0. Log: `/home/felni/tmp-cao015-root/browser-all-e2e.log`. Esta ejecución acredita navegador local; no publicación ni matriz de proveedores externos.

## Normalización mecánica

Los gates exactos de CI detectaron 43 archivos que Black reformatearía y errores de orden de imports. Se ejecutaron isort y Black solamente sobre los 69 archivos señalados, preservando su contenido. Los resultados globales posteriores y cualquier defecto adicional se registrarán al terminar la convergencia.

## Estado de ejecución — historial de convergencia

Los resultados anteriores son pruebas concretas y focalizadas. Los gates globales y la suite completa siguen en ejecución; este informe no acredita su aprobación todavía.

Primera convergencia global: `mypy src/` **exit 0, 398 fuentes, sin errores**; Black **exit 0, 1.175 archivos sin cambios**. Logs `mypy-convergence-final.log` y `black-convergence-final.log`. Los cambios posteriores de recuperación/validación remota requieren repetirlos para el cierre.

La ejecución serial parcial se sustituyó por la selección ampliada de CI con `-n 4 --dist loadfile` y cobertura aislada: incluye `test/ examples/workflow/tests/`, excluye la integración Kiro autenticada y `test/e2e`, y usa `-m "not e2e"`. `PYTHONPATH` apunta a la fuente del workspace para evitar la diferencia de mayúsculas del editable instalado en WSL. La ejecución parcial no cuenta como suite aprobada.

## Verificación posterior a las correcciones de composición

Fuentes congeladas: mypy completo **exit 0, 398 fuentes** (`mypy-final-frozen.log`); Black **exit 0, 1.177 archivos sin cambios** (`black-post-recovery.log`); isort **exit 0** (`isort-post-recovery.log`). Arquitectura **exit 0, cinco contratos** (`composition-post-recovery.log`). Clientes repetidos: Web **469 passed**, MCP Apps **92 passed**. Memoria/recuperación ampliada **1010 passed, 2 skipped, exit 0**, incluidos los 17 casos de autoridad/objetos SQLite; revisión manual final en recovery-composition-review.md.

La primera selección ampliada se interrumpió para diagnóstico después de **3591 passed, 13 failed, 6 skipped**; no es evidencia de cierre. Doce fallos provenían de fixtures de recuperación importadas antes de corregirlas durante esa ejecución. El restante era AF_UNIX path too long en una fixture Kimi: el worker alargaba la ruta temporal. La misma prueba pasó sola y con dos workers usando basetemp corto (**1 passed** en cada ejecución), sin cambiar su aserción ni el proveedor. La ejecución final usa un directorio corto exclusivo, fuentes estables y un plugin temporal que solamente registra fallos, sin modificar resultados.

Actualidad de Graphify: snapshot estructural focalizado en `graphify-out/2026-10-07-spec015-convergence/`, **108 fuentes, 5436 nodos, 11787 relaciones dirigidas, cero fuentes fallidas, 0/0 tokens**. Los hashes son iguales antes/después. Se usan extractores AST por archivo; no se ejecuta resolución entre archivos ni extracción semántica. Las tentativas previas de resolver todo el corpus se detuvieron sin producir una evidencia final y no sustituyen este alcance explícito. Se conserva el grafo completo previo y la extracción de la feature original.

La selección completa final de CI con cobertura todavía está en ejecución (`pytest-ci-final.log`). No se acredita como pasada hasta comprobar su final y código de salida.


La primera ejecución con basetemp corto fuera de TMPDIR se interrumpió: **3655 passed, 1 failed, 9 skipped**. El fallo era una fixture que exige confinamiento bajo el temp del sistema; no se debilita ese control. Reproducción aislada: **1 failed**. Con TMPDIR corto y basetemp hijo, las dos regresiones Kimi/socket y vault/confinamiento pasan juntas con dos workers: **2 passed, exit 0**. La ejecución completa vigente usa esa configuración compatible (`pytest-ci-verified.log`).

Cobertura frontend repetida con la configuración CI: **92 passed, exit 0; líneas 90.64%**, por encima del floor existente de 90.0%. Los informes se conservan fuera del árbol; el ratchet completo se ejecutará con el JSON backend final y una copia idéntica del baseline, sin reducir floors.

La selección con temporales correctos terminó al alcanzar el límite de diagnóstico: **8773 passed, 22 failed, 30 skipped**, exit 2; no acredita aprobación global ni cobertura final. Los fallos pertenecen a tres grupos: una fixture de settlement que enviaba el campo inexistente `WorkflowSpec.version`, la auditoría de rutas que no reconocía los guardas manuales actuales y mocks tmux que apuntaban al módulo anterior al transporte acotado. La fixture settlement se corrige eliminando únicamente ese campo; **50 passed, exit 0**, con las aserciones originales. La fixture tmux se repara en sus imports reales, conservando comandos, payload, timeout y cleanup: **37 passed, exit 0**, evidencia en `tmux-fixture-evidence.md`. La auditoría se amplía mediante identidad exacta del handler, llamadas ejecutables y pruebas negativas; no se cambian los guardas de producción ni se añaden exenciones abiertas. Se requiere una ejecución global nueva tras estas correcciones.

Auditoría de autorización corregida: **88 passed, 11 deselected**, incluidos dieciséis rechazos HTTP y una firma inválida con identidad registrada y grant real SQLite; los once casos integration se incluyen en la selección global ampliada. Revisión y límites en `types-composition-review.md`. La revisión independiente y la ejecución de seis archivos adicionales con mocks antiguos (`tmux_work_create`, `tmux_pane_spawn`, `tmux_lookup_error`, `tmux_exact_cleanup`, `tmux_client`, `path_validation`) obtienen **270 passed, 1 skipped, 3 deselected, exit 0**: esos mocks no afectan las rutas ejercitadas y no se cambian por limpieza especulativa.

La nueva selección completa usa `pytest-ci-complete.log` y cobertura exclusiva `coverage-ci-complete`; comenzó con hashes congelados de **1180 archivos Python**, después de terminar todas las correcciones de fixtures. Graphify conserva **108 fuentes actuales**. Los gates frescos `mypy-all-final.log` y `composition-all-final.log` terminan **exit 0**, con **398 fuentes** y **cinco contratos** respectivamente; Black/isort posteriores a las fixtures terminan **exit 0**, con **1177 archivos** comprobados por Black. Esta ejecución ya terminó; el diagnóstico y las correcciones posteriores se registran a continuación.

## Diagnóstico completo posterior y cierre de cobertura

`pytest-ci-complete.log` terminó **16172 passed, 16 failed, 110 skipped, 91 warnings**, exit 1, en 2568.05 s. Es diagnóstico completo, no aprobación. Cobertura real de la selección completa: **85.534247%**, 62576/73159 líneas; el ratchet existente exige **87%**. El baseline permanece idéntico y no se reducen floors ni inclusiones. El informe MCP Apps conserva **90.64%**, superior a 90%.

Los dieciséis fallos se reprodujeron y corrigieron: documentación de confianza de plugins (29 passed), fixture cwd del transporte tmux acotado (21 passed), parada confirmada en eventos de plugins con dos regresiones de parada incierta (20 passed), contexto requester para dispatch al curator (18 passed), captura antes de parar el terminal (11 passed), aislamiento de registry en la fixture de lifespan (4 passed), catálogo Click/Rust con nueve comandos nuevos HIDE (4 Python passed y 223 Rust passed), y replay AG-UI con historial retenido real y resync explícito (10 passed). Las fixtures conservan las aserciones y los guardas reales. Los informes especializados explican cada frontera. Estos resultados específicos requieren una selección global nueva.

T067 añade pruebas funcionales de autoridad de pares, snapshots y procesos/backends, conservando los efectos nativos simulados solamente en la frontera externa. Pruebas parent Bubblewrap: **59 passed** en 70.05 s, con **184 líneas previamente faltantes** ejecutadas; incluyen pipes reales, límites de mensajes, memfd sellado, identidad y limpieza de dos procesos propios mediante pidfd. No acredita namespaces Bubblewrap reales. Docker: **78 passed** en 53.57 s, con **310 líneas previamente faltantes**; SQLite, contenido ELF y validaciones de autoridad reales, CLI Docker simulado. No acredita un daemon desplegado. Los incrementos son diagnóstico de cobertura y no sustituyen el JSON de la selección global final.

Las nuevas pruebas SQLite descubrieron T068: el consumidor curator buscaba `session_name`, mientras el inventario publica `tmux_session`. La corrección debe preservar la restricción por sesión y quedar cubierta por despacho y fallback reales. Los gates finales y el ratchet global quedan pendientes hasta congelar esta corrección y repetir la selección completa.

Fuentes y pruebas congeladas después de las últimas fixtures/imports: **1198 archivos Python/Rust**, hashes iniciales en `rerun-source-hashes.json`. La tentativa `pytest-ci-rerun.log` se interrumpió durante colección para corregir únicamente el orden de imports de la nueva prueba Docker; no cuenta como ejecución ni cobertura aprobada. La selección vigente es **`pytest-ci-rerun-final.log`**, con cobertura propia **`coverage-ci-rerun-final`** y TMPDIR corto/basetemp hijo.

T068 pasa la suite amplia de memoria/archivo/vault/recuperación: **1101 passed, 2 skipped, 7 warnings, exit 0**. Revisión independiente de snapshots/pares/curator: **141 passed, sin P1/P2**; procesos/Docker revisados sin P1/P2. Véase [coverage-contracts-review.md](coverage-contracts-review.md). Cobertura focalizada de pares: **68 passed**, 429 líneas antes faltantes; snapshots/curator: **91 passed**, 221 líneas antes faltantes. La unión diagnóstica real de reportes mide **87.0979647%**; incluye un diagnóstico con fallos y no acredita cierre global.

Gates posteriores a la corrección curator: mypy completo **398 fuentes sin errores, exit 0** (`mypy-rerun-final.log`), Black **1186 archivos sin cambios, exit 0** (`black-rerun-final.log`), isort **exit 0** (`isort-rerun-final.log`), composición **400 archivos / 1680 dependencias / cinco contratos conservados / cero rotos, exit 0** (`composition-rerun-final.log`). Graphify renovado: **110 fuentes / 5506 nodos / 11964 relaciones dirigidas / cero fallos / 0/0 tokens**, hashes de fuente verificados nuevamente. El alcance sigue siendo extracción AST por archivo, sin resolución entre archivos ni semántica.

Conformidad offline de plugins: schemas vendorizados y ambos paquetes **PASS, exit 0**; frontera Rust sin FFI Python: **110 crates inspeccionados, PASS, exit 0**. El primer intento no-FFI no encontraba Cargo en PATH y falló cerrado; se repitió con el binario existente, sin modificar el detector. Catálogo e integración Rust actualizados: [catalog-evidence.md](catalog-evidence.md).

## Diagnóstico de recursos WSL y repetición nativa

`pytest-ci-rerun-final.log` terminó **16467 passed, 3 failed, 110 skipped, 94 warnings**, exit 1, en 2561.12 s. Las tres trazas fallan durante imports del filesystem Windows con **OSError ENOMEM**, incluida la enumeración de `libtmux/_internal` y `mcp/client`; una lectura independiente `du` del entorno falla del mismo modo. La prueba de comportamiento no llega a ejecutarse en esos subprocesos. Linux mostraba 13 GiB usados, swap 4 GiB lleno y cuatro workers con aproximadamente 2.5 GiB residentes; no se cambió configuración del host ni se detuvieron procesos ajenos. Su cobertura completa **87.0883965%** supera el floor, pero la ejecución con fallos no cuenta como aprobación.

Se copió el estado actual, incluidos Git, fuentes, pruebas, fixtures, docs y locks, a `/home/felni/tmp-cao015-root/linux-validation/`; se omiten únicamente entornos/caches/builds y el grafo no consumido por estas pruebas. **1198 hashes Python/Rust idénticos** en workspace y copia. No se hizo checkout ni commit de contenido distinto. `uv sync --locked --all-extras --dev --python 3.12.13` instaló el lock existente en una venv Linux propia, sin cambios al proyecto; 140 paquetes resueltos y 127 instalados. Ambos archivos afectados pasan allí completos: **10 passed en 34.85 s, exit 0**, sin cambios de pruebas ni código (`native-memory-reproduction.log`).

La selección global vigente es **`pytest-ci-native-final.log`**, misma inclusión/markers/exclusiones de CI, con **dos workers**, temp corto/basetemp hijo y cobertura exclusiva **`coverage-ci-native-final`**. Debe terminar sin fallos antes de aprobar T064/T067.

## Cierre local verificado

T001–T068 completadas. **`pytest-ci-native-final.log` terminó exit 0**: **16470 passed, 110 skipped, 83 warnings en 3541.20 s (59:01)**. La selección conserva todos los directorios, markers y exclusiones de CI; solamente reduce a dos workers y ejecuta el mismo contenido en un filesystem Linux para evitar ENOMEM del montaje Windows. No se modificaron pruebas para tolerar el fallo ambiental. La venv propia usa **Python 3.12.13 y `uv sync --locked --all-extras --dev`**.

| Control | Resultado final | Evidencia |
|---|---|---|
| Selección Python completa de CI, incluidas integraciones locales | PASS — 16470 aprobadas, 110 omitidas, cero fallos | `pytest-ci-native-final.log` |
| Cobertura backend | PASS — 63713/73159 líneas, **87.0883965%**, floor **87%** | `coverage-ci-native-final`, XML y JSON final |
| Cobertura MCP Apps | PASS — 475/524 líneas, **90.64%**, floor **90%** | `mcp-apps-coverage.log` y JSON-summary real |
| Ratchet de ambos informes | PASS — exit 0, sin ausencia/skip de informe | `ratchet-final.log` |
| Mypy completo, configuración efectiva `mypy.ini` | PASS — 398 fuentes, cero errores | `mypy-rerun-final.log` |
| Black / isort | PASS — 1186 archivos comprobados por Black, exit 0 de ambos | `black-rerun-final.log`, `isort-rerun-final.log` |
| Arquitectura/composición | PASS — 400 archivos, 1680 dependencias, 5 contratos conservados, 0 rotos | `composition-rerun-final.log` |
| Web / MCP Apps / navegador MCP Apps | PASS — 469 / 92 / 7 pruebas | `web-convergence.log`, `mcp-apps-convergence.log`, `browser-all-e2e.log` |
| TUI catálogo y bins | PASS — 141 comandos; 24 InApp / 18 Handoff / 99 Hidden; 4 Python y 223 Rust | [catalog-evidence.md](catalog-evidence.md) |
| TUI integraciones con API local aislada | PASS — 36 pruebas | `tui-catalog-integration.log` |
| Rust fmt / Clippy / frontera sin FFI Python | PASS — exit 0; 110 crates examinados en no-FFI | catálogo y `rust-no-ffi-final.log` |
| Schemas y paquetes Agent Plugins | PASS — offline, ambos paquetes válidos | `agent-plugins-conformance-final.log` |
| JIT / presupuesto de bundles MCP Apps | PASS — escaneo limpio y cuatro bundles dentro de límites | `jit-final.log`, `bundle-size-final.log` |
| Revisiones independientes | PASS sin P1/P2 abierto en fronteras revisadas | informes de composición, recuperación y [coverage-contracts-review.md](coverage-contracts-review.md) |

Los logs del padre se encuentran en `/home/felni/tmp-cao015-root/`; los informes por historia indican los logs especializados. El baseline conservado tiene SHA-256 **413b7af75bfa7dc3b1c3908563f7ae13c19d6c7a3d0b162d35652c46d025590d**. El JSON backend final declara cobertura.py **7.10.7**, timestamp **2026-10-07T07:03:38.652142**. No se usa la unión diagnóstica como gate final.

Actualidad final: **1198 hashes Python/Rust idénticos** en workspace y copia probada. El snapshot Graphify mantiene **110 fuentes, 5506 nodos, 11964 relaciones dirigidas, cero fallos, 0/0 tokens** y coincide con los hashes de fuente actuales. Es AST por archivo, sin resolución entre archivos ni inferencias semánticas; las conclusiones de integración se sustentan en fuente y checks ejecutados. Resultado guardado en `source-freshness-final.json`.

Las **110 omisiones** están detalladas en el resumen `-ra` y ponderadas en `native-skips-final.json`; no cuentan como aprobadas. Incluyen aceptación nativa Bubblewrap/Docker que exige binario/runner/imagen aprobados u opt-in, capacidades Landlock/dispositivos ausentes, gitleaks no instalado, fish/zsh ausentes y proveedores live deshabilitados. Un caso requiere Cargo en PATH del job Python; Rust se comprobó por separado con su binario existente. La matriz Python 3.10–3.14 declarada en CI, escáneres remotos, cuentas de proveedores y publicación no se ejecutaron en esta validación local. No se hizo commit, push ni publicación y se conserva el trabajo previo.

Validación documental y diff final después de registrar el cierre: **Markdown links PASS, exit 0** (`markdown-closed-final.log`) y **git diff --check PASS, exit 0** (`diff-closed-final.log`). Inventario final: **68 tareas, 68 completadas, ninguna pendiente ni ID duplicado**.
