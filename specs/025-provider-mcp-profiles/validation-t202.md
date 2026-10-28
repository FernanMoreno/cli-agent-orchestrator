# T202 · Cierre EOF explícito: recorrido correcto, aceptación global pendiente

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
