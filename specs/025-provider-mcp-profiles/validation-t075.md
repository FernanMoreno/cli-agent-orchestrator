# T075: fixtures OpenCode independientes del estado heredado

Fecha: 2026-10-22. Base: `9213df6f742f5977f87c2fa344fe621175ec1d3a`. FR058/SC042. No se modifica código de producto.

## Inventario y primera frontera incorrecta

El AST actual localizó inicializadores en 17 módulos `test/providers/test_opencode*.py`. El grafo existente aporta el contexto provider/configuración y se contrastó con fuente; no contiene todos los helpers recientes. Las fuentes que importan `launch` incluyen también `test/providers/test_mcp_managed_surface_binding.py` y `test/services/test_opencode_profile_preallocation.py`.

| Grupo | Estado anterior y corrección |
| --- | --- |
| Shared `launch` y consumidores de perfiles, filtros, reinicio, auth v1 y preasignación | Aislaba XDG_DATA_HOME y DB, pero heredaba inline-auth y aliases de configuración. Ahora depende del fixture común explícito. |
| `installed_v1` y `runtime` | Las tres líneas locales de T074 se sustituyen por la misma dependencia, preservando sus pruebas y aserciones. |
| Inicializadores directos y casos de rechazo temprano en `test_opencode_v2.py` | No tenían una preparación común; el módulo solicita el fixture. Sus overrides sintéticos deliberados se aplican después. |
| CLI `install_workspace` y consumidores importados | Ya redirigía sus rutas; depende ahora del fixture común para que sus bindings específicos ocurran después de los defaults compartidos. El control nativo de help/list conserva su entorno privado explícito. |
| Utilidades de configuración, política y ownership | Los helpers de archivos reciben rutas temporales explícitas o aliases redirigidos por sus propias fixtures. Los casos puros no invocan lectores de estado. No se cambia su comportamiento. |

Producción conserva correctamente los overrides del entorno. El defecto estaba en las fixtures: un runtime falso no elimina `os.environ`, y las constantes de configuración ya importadas no se redirigen al cambiar XDG. No procede modificar el login, la selección de autenticación ni los defaults de producto.

## Reproducción y corrección

Cuatro casos RED fallaron con un padre sintético y guardas de lectura: v1 copió inline-auth heredada; v1 leyó configuración gestionada heredada; dos inicializadores v2 intentaron abrir la DB heredada. Los sentinels sintéticos permanecieron intactos. La primera instrumentación no desactivó su propia guarda al comprobar sentinels/limpiar al terminar; se corrigió ese problema del diagnóstico y se conservaron ambos recibos. La reproducción válida con finalización completa volvió a dar cuatro fallos antes de editar las fixtures.

El nuevo `opencode_test_environment` en `test/conftest.py` es explícito, no autouse global. Crea cuatro raíces XDG temporales, retira overrides de autenticación/configuración del proceso de prueba y redirige los aliases gestionados del provider y del lector de configuración. No cambia HOME, PATH ni fixtures de otros proveedores. Las pruebas pueden introducir sus overrides explícitos después de setup. Ubicarlo en la raíz de tests permite resolverlo desde consumidores que importan `launch` fuera de `test/providers`.

Los mismos cuatro casos pasaron después del cambio, con guardas activas y sentinels intactos. Black e isort pasan para los siete archivos Python cambiados. Se mantiene toda aserción previa.

## Validación ampliada en curso

La selección anterior completa más las dos regresiones iniciales y los consumidores importados indicados pasó: **899 pruebas, cuatro avisos y cero fallos**, en 148.47 s, con dos workers y cobertura configurada. El padre conserva XDG, DB, inline-auth, config nativa y aliases gestionados sintéticos contaminados; una guarda de auditoría rechaza abrir los archivos del supuesto operador durante las pruebas. No se usa un runner vacío para atribuir hermeticidad. Los escenarios explícitos de auth/override siguen en la selección. Los tres recibos de la guarda (padre y ambos workers) registraron cero accesos a los archivos heredados y exit 0; los sentinels permanecieron intactos.

La segunda revisión de Claude aprobó ownership y orden de fixtures; recomendó reforzar la prueba duradera. Se añadió una guarda os.open además de Path.open y dos variantes v2 para DB heredada mediante XDG o override. Comprueban ausencia de copia privada, fuentes intactas y HOME/PATH preservados. Esta ampliación de pruebas y los dos módulos vecinos mixtos de estado/reinicio se validan por separado; no se cambió ninguna fixture desde la ejecución ampliada. El seguimiento pasó: **165 pruebas, cero fallos**, en 101.41 s; incluye las cuatro variantes duraderas y los dos módulos vecinos mixtos. Sus tres recibos de guarda también registraron cero accesos heredados y exit 0, con sentinels intactos. Estas variantes v2 añadidas después del fix refuerzan de forma duradera el contrato; la evidencia RED v2 procede de los dos tests existentes ejecutados antes del cambio, no de esas nuevas variantes.

No se abre estado real del operador, no se generan mensajes con modelos, no se ejecuta QEMU ni se actúa sobre jobs históricos. La aceptación nativa/global y la matriz completa de autenticación quedan fuera de este cierre de fixtures.

## Revisión de composición y cierre

Subsistemas afectados: exclusivamente fixtures de tests, sus consumidores de preparación v1/v2 y la composición instalación→configuración→lanzamiento. Se verificaron propiedad y orden de setup, restauración mediante monkeypatch, ausencia de copias/lecturas heredadas y preservación de overrides explícitos. Los dos módulos vecinos añadidos usan estados puros o sustituyen `_initialize_validated` antes del acceso a estado. No hay cambios en contratos HTTP/MCP, grants, esquemas ni runtime de producto; no corresponde crear otro contrato externo o migración.

`project-composition-check "$(cat .ai/project-name)"` pasó con 412 archivos, 1834 dependencias y cinco contratos conservados, ninguno roto. Black/isort se verificaron de nuevo después de la última ampliación de guardas: siete archivos Python correctos. Se reutilizó la identidad verificada del PostgreSQL existente con autostart desactivado. La revisión final de Claude, solo lectura y sin ejecutar tests, no encontró bloqueos; se incorporaron sus aclaraciones sobre fuentes de evidencia y alcance.

Las guardas duraderas cubren Path.open y os.open, las rutas actuales de lectura del producto; no interceptan por sí solas un futuro builtin open/io.open/driver SQLite. La guarda de auditoría del recorrido ampliado sí cubrió las aperturas de archivos del padre sintético. Los overrides de configuración nativa y las raíces XDG cache/state quedaron cubiertos en esa ejecución contaminada; no se atribuyen a los casos duraderos de DB/inline/config gestionada. No se extrapola el resultado a código o fixtures futuros.

Veredicto: PASS para FR058/SC042 y T075. La diferencia de producto es vacía; se conservan byte a byte los doce artefactos Graphify ajenos. No cambian relaciones/imports de producto, por lo que no se refresca el grafo compartido. El conocimiento es evidencia específica del repositorio, sin motivo para copiarlo al vault. Permanecen separados los gates globales y la matriz nativa de perfiles/modelos/autenticaciones.
