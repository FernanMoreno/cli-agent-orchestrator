# T202 · Recorrido real aceptado con cierre EOF explícito

## Resultado actual: r4 aceptado

El propietario da accepted=true en **5fddafaf**, con510fuentes fijadas.
Claude2.1.303 realizó dos send_message correctos; se verificaron dos filas exactas
PostgreSQL, dos eventos AG-UI redactados, un reinicio de API y la identidad/turnos/
recibos causales. Perfil collaboration, filtro send_message y flag EOF real se
atestaron antes de ambas fases. El cierre solicitado por el harness produjo un
sello normal genuino y exitHTTP200 dentro del mismo presupuesto de6s.

La admisión observó60,188s con14→14contenedores y0eventos create/destroy.
Los inventarios privados antes/después acreditan14→14 y0faltantes. Las37identidades
observadas y el PG/contenedor/red propios quedaron retirados; fuentes/binario y
login originales intactos, staging de credenciales retirado. Total178,052s, mínimo
9324MiB. Descriptor recerrado; archivo ejecutado y evidencia fallida anterior intactos.
[Recibo aceptado r4](validation-t202-native-r4.json).

**T202 cerrado sólo para el modo explícito `harness_requested_eof`.** El recibo
mantiene native_automatic_shutdown_accepted=false. T197 automático, Work, ACK exacto
del receptor, matriz completa de perfiles y gates globales siguen separados.
No se reconstruye el cliente que eliminó el contenedor de r3 ni se convierte ese
fallo en éxito. Los apartados siguientes conservan la historia y sus límites.

## Implementación y validación offline (historia previa a r4)

Se añade una opción estricta 0/1 para Claude, desactivada por defecto, que cierra
stdin mediante el transporte ya existente antes de pedir exit. Exige exactamente
dos llamadas exitosas y sello normal real dentro del mismo presupuesto compartido
de6s; no usa fallback ante fallo. El catálogo, perfil collaboration, filtro
send_message, permisos y comportamiento productivo permanecen iguales.

El recibo distingue petición, resultado cerrado de EOF y shutdown_mode;
native_automatic_shutdown_accepted siempre es false. T197 automático sigue abierto.
No acredita contrato externo Work, ACK exacto del receptor ni todos los perfiles.

Validación offline:19RED/1control→268PASS en113,22s en cinco módulos;44 controles
enfocados finales. Se corrigió una expectativa histórica de test Codex: con dos
resultados unknown la versión HEAD ya rechazaba EOF y no esperaba sello; se
preserva ese rechazo, sin rebajar el contrato. Sin PostgreSQL/modelos en esa suite.
Black/isort pasan. Composición PASS, contratos de arquitectura5/0. Revisión Claude
GO tras resolver dos ambigüedades del diagnóstico. El revisor no ejecutó pruebas.

Composición revisada: selector→config/env→stdio→sello→exit→cleanup→recibo. Plazo
único, tipos estrictos, modo predeterminado intacto, rechazo antes de efectos,
fallos parciales y compensación conservados. Graphify no indexa estos fixtures;
se verificaron los enlaces contra código sin regenerar los cambios ajenos del grafo.

Control nativo sin mensajes preparado y cerrado:11controles/19imports reales,
propietario PG no adquirido, sin credenciales preparadas. Se probará EOF con audit
vacío antes de gastar dos turnos de modelo. La preparación NO es aceptación nativa.


Control nativo previo en dd558917: EOF y sello normal reales, exitHTTP200 dentro
de6s; CLI/MCP ausentes antes de DELETE.0llamadas/filas/eventos/reinicios,27identidades
retiradas,508pins intactos, PG/contenedor/red cerrados, login original intacto y
staging retirado.86,004s/mínimo9269MiB. Descriptor recerrado, archivo ejecutado
inalterado. Resultado failed phase_a deliberado antes del primer trigger; no es
aceptación de mensajes. [Recibo público](validation-t202-probe.json).


## Recorrido completo r3: gate global fallido, cierre funcional correcto

Fuente dd558917,508bindings,140,557s/mínimo8883MiB. Dos send_message del modelo
SUCCESS,2filas exactas PostgreSQL,2eventos AG-UI redactados,1reinicio de API,
identidad/terminal/turnos/recibos causales verificados. Perfil collaboration y
filtro send_message, incluido flag EOF real, atestados en ambas fases.
EOF solicitado explícitamente, sello normal real y exitHTTP200 dentro del plazo
compartido original6s.35identidades retiradas, PG/contenedor/red propios cerrados,
fuente/login originales intactos y staging retirado. [Recibo](validation-t202-native-r3.json).

El recorrido interno da pass; accepted del propietario es FALSE porque
original_containers_preserved es false. No se rebaja ni reescribe ese gate.
Docker retuvo kill/die/destroy de un postgres:16-alpine con etiqueta Testcontainers,
diferente del PG propio, alrededor de17,5s; el PG propio se retira alrededor de139,5s.
La fuente de cleanup sólo admite su identidad/nombre/labels exactos. También se
observó actividad reciente de creación/destrucción de otros Testcontainers.
No se guardó el conjunto inicial de IDs, por lo que no puede reconstruirse la
identidad exacta faltante ni atribuirse el cliente Docker que la retiró. Eventos
crudos permanecen privados; no se toca ningún contenedor de otro trabajo.

T202 sigue abierto por aceptación global del nuevo modo. Antes de otra ejecución
se necesita estabilidad del inventario ajeno y capturar inventarios antes/después;
no repetir dos turnos de modelo durante esa actividad ni relajar preservación.
T197 automático también sigue abierto: este éxito de cierre corresponde sólo a
harness_requested_eof. Work/ACK exacto receptor/perfiles/global siguen separados.
Ambos descriptores reales recerrados y sus archivos ejecutados inalterados.

Obsidian actualizado mediante transacción save-20261028-caos-claude-eof-boundaries: nota de límites, índice/registro y hot sin cambios; lint estricto0hallazgos. Próximo gate revisado en lectura: supervisión Codex con objetivos reales; T173 catálogo no equivale a autoridad por operación. No se ejecutó ese caso.


## Inventario preservado y reintento bloqueado antes de recursos

La captura se integra como helper compartido en
`test/live_provider/native_container_inventory.py`, con16pruebas PASS/27,74s
tras RED por módulo ausente. Una consulta read-only por inventario proyecta sólo
ID/nombre/imagen/sesión Testcontainers; valida campos/tipos/IDs únicos y conserva
JSON0600 exclusivo con lectura real de comprobación. Rechaza filas inválidas,
metadatos adicionales, duplicados, fase inválida antes de consultar Docker,
sobrescritura, modo incorrecto y contenido alterado. No lee Env/Command/Labels
completas. Los nombres/sesiones no prueban quién borró un contenedor.

El adaptador r4 delega al helper canónico antes de auth/servicios y después de
cerrar el propietarioPG. Conserva original_containers<=after y todos los gates.
19imports reales,7grupos de controles de inventario,8de ámbito,4de viewport,
9de modo/recibo y dos consultas Docker con writer/reader real pasan. En las dos
últimas consultas se observaron23→22contenedores; eso NO es estabilidad.
La primera expectativa privada de error por sobrescritura se corrigió al contrato
real AcceptanceFailure(cleanup); no se cambió el writer ni se adquirieron recursos.

Dos ventanas completas de60,177s y60,317s FALLAN:15→16/24eventos y15→24/66eventos
de create/destroy. El armado exige estabilidad,0eventos,60s observados, edad≤120s
y que ambos inventarios y el actual coincidan. El propio arm rechazó el entorno
actual antes de manifest/archive/credenciales/PG/modelo. Descriptor r4 CERRADO.
No se lanza otro recorrido ni se reescribe r3. [Resultados](validation-t202-inventory.json).

Los268tests anteriores corresponden a los mismos cuatro archivos sin cambios,
no a una nueva ejecución. Composición/arquitectura5-0 y formato/imports pasan
para este incremento. La revisión de composición verifica productor Docker→JSON
privado→comparación original→recibo público de conteos; errores no otorgan aceptación.
Si el recorrido/PG lanza antes de la captura final, ésta puede faltar; se conserva
el fallo y no se atribuye un cliente borrador. Entre arm y ejecución debe evitarse
espera; la preservación final sigue obligatoria aunque la admisión haya pasado.

Revisión Luna del SIGINT: AnyIO puede cancelar handlers y lanzar KeyboardInterrupt;
normalizarlo en el servidor podría producir un sello engañoso de operaciones
canceladas. No se aplica ese atajo. T197 automático queda separado de EOF explícito.
T202 permanece abierto mientras no pase su gate global con el inventario preservado.

Revisión Claude final GO del helper/test/delegación; no ejecutó comandos. Obsidian actualizado mediante save-20261028-caos-container-inventory-admission (nota/índice/registro, hot intacto), lint estricto0hallazgos.

Revisión final de aceptación Claude GO: recibo/alcance/listas consistentes; no ejecutó comandos ni pruebas. Composición actual5contratos/0rotos PASS. Obsidian actualizado con save-20261028-caos-t202-eof-acceptance (nota/índice/registro; hot intacto), lint estricto0hallazgos.
