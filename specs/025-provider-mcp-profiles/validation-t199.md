# T199 · aviso inicial Security notes de Claude

Corrección productiva acotada, aceptación real pendiente. T198 retuvo el
viewport completo antes de compensar y reprodujo provider_init_timeout.
El handler no reconocía la continuación informativa. El helper nuevo exige
último banner nativo, ambas notas en orden, URL pública y pie exactos, sobre
viewport actual y con strip_terminal_escapes productivo. Confirma una sola
vez; no trata el aviso ni su acuse como readiness. Conserva gates de estado,
MCP, OAuth, permisos y plazos. Mientras estas notas están en history, usa la
lectura visible también para los handlers siguientes: un trust histórico no
puede interceptar la pantalla actual.

TDD inicial2RED/10controles PASS;17PASS enfocados tras corrección. La primera
regresión247PASS/180,93s no cerró el fallo: la revisión encontró que el helper
exigía banner en primera línea y el fixture publicado era sólo el sufijo UI.
El pane real conserva shell/MOTD delante. Se reprodujo contra la captura
completa privada y con un test nuevo de prefijo sintético (AssertionError).
Ahora selecciona el último banner y niega notas anteriores a un banner nuevo;
el replay de la captura completa y ambos controles pasan. Se repite la misma
regresión ampliada tras esta corrección; no se etiqueta247PASS como bytes finales.

El fixture conserva las27líneas desde el primer banner real, después de quitar
escapes con el parser productivo. Se revisó antes de publicarlo: sólo arte,
notas, URL pública y footer, sin rutas/echo/login/tokens. El prefijo real se
reproduce además privadamente; su contenido no se publica. No se infiere
soporte para pantallas recortadas, otro idioma o texto/URL/pie distintos; esos
controles niegan Enter. La fixture no representa toda la entrada del productor.

Composición revisada: initialize -> handler -> backend visible -> StatusMonitor
-> wait_until_status -> MCP readiness/API compensación. Un idle-gap del handler
sigue siendo distinto de completar initialize: el segundo mantiene su gate
positivo de estado. Sin API/esquema/DB/migración/dependencias nuevas; Pact no
aplica al aviso local. Arquitectura5-0; Graphify usa source_file y source_location
separados, identifica las aristas del proveedor pero sus offsets están antiguos
y se contrastan con fuente. Sus12archivos ajenos permanecen sin tocar.

Obsidian conserva causa y prevención mediante save-20261028-caos-startup-before-compensation,
nota de fronteras/index/log; hot intacto y lint estricto0hallazgos. No reconstruye
T197r1 ni el504histórico. T197/T011/globales siguen abiertos hasta su alcance real.


Verificación final:249PASS/181,61s en los mismos cinco módulos (nuevo aviso,
Claude unit/coverage, startup idle-gap y MCP readiness), incluidos21controles
del aviso. Cobertura/opciones intactas y PostgreSQL propio retenido hasta salir
pytest; la invocación usa COVERAGE_FILE privado exclusivo. Black/isort/diff PASS,
5contratos conservados/0rotos y revisión ClaudeGO tras el hallazgo de prefijo.
El lead comprueba que los3hashes revisados coinciden con los bytes finales;
la revisión read no ejecuta los tests. Veredicto offline PASS, real pendiente.
La primera invocación del propietario usó _docker sin el ejecutable y no llegó
a pytest; se corrigió el lanzador antes de obtener RED. El primer stdout a .log
no quedó retenido; sólo red-retained.txt acredita2RED/10PASS. Ambos se separan
de la evidencia válida, sin repetir modelo ni atribuirlos al producto.
