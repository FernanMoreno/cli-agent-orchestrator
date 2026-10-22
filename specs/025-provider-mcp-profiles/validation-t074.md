# T074: ciclo de vida de la copia privada de estado OpenCode

Fecha: 2026-10-22. Base: `667d66e53403d9b761bd81ba9910d8ec15c350ea`. Alcance: FR057/SC041 en Spec025, distinto de T074 host/guest en Spec008.

## Reproducción y primera frontera incorrecta

Seis pruebas fallaron antes del cambio de producto:

- Tres hijos acotados a diez segundos se bloquearon al abrir una FIFO sin escritor: inspección de metadata, copia de archivo y bundle con base regular/WAL FIFO. El `open(O_RDONLY)` esperaba antes de alcanzar `fstat`; un timeout async exterior no interrumpe esa llamada síncrona.
- Un fallo de mkdir después de abrir el origen dejó un descriptor abierto. El mkdir estaba fuera del finally del origen.
- Un fallo de fchmod dejó abierto el descriptor de destino. La llamada precedía al finally del destino.
- Una colisión O_EXCL eliminó el destino preexistente. El manejador de errores hacía unlink sin comprobar que esa llamada hubiera creado el archivo.

Los casos iniciales RED están conservados en evidencia privada. Usan únicamente bytes sintéticos y rutas temporales; no leen ni modifican el estado de autenticación del operador. Claude confirmó las cuatro causas mediante lectura del helper y los consumidores v1/v2; no ejecutó las pruebas.

## Corrección y contratos

Los dos lectores abren el origen con O_NONBLOCK y conservan O_NOFOLLOW; después exigen un archivo regular. La copia protege mkdir con el finally del origen y fchmod con el finally del destino. Solo borra el destino si su O_EXCL tuvo éxito. El bundle conserva los tres intentos, comparación de metadata, traducción de nombres, regeneración de SHM y limpieza de copias parciales.

La preparación privada de v1 y v2 comparte este helper. No cambian sus reglas de precedencia de autenticación, modelo, entorno o permisos. El grafo existente ubica el provider y sus consumidores; no contiene estos helpers recientes, cuyas llamadas se contrastaron directamente con la fuente actual. No se modifican dependencias ni APIs; no se refrescan los artefactos Graphify ajenos.

## Regresión y aislamiento de fixtures

Las primeras once pruebas GREEN pasaron, incluyendo copia/cancelación y preservación de colisiones regulares/symlink en archivo/bundle. Se agregaron dos escenarios de estabilidad WAL: cambio tras la primera copia y agotamiento con fallo parcial, incluidos en la regresión ampliada en curso. Black e isort pasan en los dos archivos Python. La primera selección ampliada terminó con 851 pruebas correctas y ocho fallos de fixtures. Se conservaron esos resultados y se investigaron sin atribuirlos a la corrección.

Las ocho trazas llegaron a la exportación de credenciales con un ejecutable ficticio: dos fixtures aislaban configuración/versión, pero heredaban el directorio de datos y los overrides de autenticación. Un replay en memoria de los dos helpers originales, con una base sintética heredada, reprodujo exactamente ocho fallos y dos éxitos. Al aislar XDG_DATA_HOME y retirar OPENCODE_DB/OPENCODE_AUTH_CONTENT en esos dos fixtures, los mismos diez casos pasaron con el estado sintético heredado intacto. No se retiraron ni suavizaron aserciones. Se eliminaron tres archivos temporales de estado generados por la primera suite bajo su namespace pytest; no se modificó la fuente del operador. La repetición de la selección completa usa además un directorio de datos vacío propio. Este entorno seguro no demuestra que todas las demás fixtures sean herméticas por sí solas; la revisión adicional de esas fixtures queda separada.

Claude aprobó en lectura la implementación y sus trece controles de filesystem. La selección completa, idéntica a la inicial, terminó con **859 pruebas correctas, cuatro avisos y cero fallos/skips** en 144.43 s; incluye los trece escenarios nuevos. Se mantuvieron los filtros originales y la recogida de cobertura configurada. Los diez controles de fixtures con estado sintético heredado pasaron por separado. La revisión final de Claude aprobó las seis líneas de aislamiento y su atribución causal, sin ejecutar comandos. Composición pasó: 412 archivos, 1834 dependencias y cinco contratos conservados, ninguno roto.

La aceptación nativa/global, la matriz completa de autenticación/perfiles/modelos y los fallos históricos del upstream quedan separados. Esta corrección de filesystem no acredita generación, mensajes ni aceptación de un login real.

## Revisión de composición y límites

Subsistema cambiado: lectores de estado privado del provider. Vecinos contrastados: preparación de autenticación v1, aislamiento DB/WAL v2 y preparación/limpieza de directorios del lanzamiento. Invariantes comprobadas: propiedad de descriptores y archivos, fallo parcial, cancelación, colisión y no seguimiento de enlaces, snapshot estable y agotamiento de reintentos. El API, los mensajes, los grants y los esquemas no cambian; no corresponde un nuevo contrato HTTP. La suite usó el PostgreSQL existente verificado, con autostart desactivado; las nuevas reproducciones de filesystem no necesitan base de datos.

Arquitectura: `project-composition-check "$(cat .ai/project-name)"` termina en PASS. Black e isort pasan para los cuatro archivos Python afectados. Tres rondas de Claude en modo lectura cubrieron diagnóstico, implementación y las dos fixtures; sus resultados ejecutables proceden del líder. Los doce archivos Graphify ajenos se conservan byte a byte. No hay nuevos imports ni dependencias de producto; no se requiere actualizar el grafo compartido para este cambio local.

Veredicto: PASS para FR057/SC041 y T074. La auditoría de hermeticidad de otras fixtures queda en T075; un entorno vacío del runner no la acredita. La limpieza temporal se limitó a archivos de estado dentro del namespace `/tmp/pytest-of-felni/pytest-667/`, en directorios de runtime `opencode-v1`/`opencode-v2`; no se limpiaron datos del operador. Los ocho fallos iniciales, el replay original y los resultados finales se conservan como evidencias distintas. No se ejecutaron recorridos de generación/mensajería ni QEMU, no se tocaron jobs históricos y no se amplió la aceptación nativa/global.
