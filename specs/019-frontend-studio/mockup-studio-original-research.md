# Maqueta «Mesa de despacho»: investigación y dirección

Fecha: 2026-10-22. Sustituye como propuesta a `mockup-red-multiagente.html`, que se conserva para comparar. Archivos: [HTML](mockup-studio-original.html), [escritorio 1440](mockup-studio-original.png), [móvil 375](mockup-studio-original-movil.png).

Es una maqueta estática con datos ficticios. No hay backend, API, proveedor, modelo ni agente conectado; las identidades, perfiles y autoridades son inventados. No cambia el frontend de producción.

## Referencias consultadas

Las páginas se descargaron por HTTP público el 2026-10-22 y se leyeron texto e imágenes. No se copió maquetación, marca, assets ni código.

| Referencia | Qué se observó | Cómo se transformó |
| --- | --- | --- |
| Linear, «A calmer interface for a product in motion» — https://linear.app/now/behind-the-latest-design-refresh (imágenes del artículo: capas App / Location bar / View bar / Content y barra lateral atenuada) | «No competir por atención no ganada»: la navegación se atenúa para que el área de trabajo mande. «La estructura se siente, no se ve»: menos separadores, bordes suaves. Menos iconos y más pequeños. Cabecera en capas con acciones en lugares predecibles. | Barra lateral en tono más oscuro que el lienzo, texto apagado e iconos de trazo fino de 15 px. Tres capas propias: barra de ubicación (proyecto/instancia, búsqueda, aviso de maqueta), barra de vista (Grafo/Lista, filtros, estado) y contenido. Separación por planos de color y sombras de 1 px en lugar de cajas con bordes duros. La paleta es cálida y clara, no el modo oscuro índigo de la referencia. |
| Vercel Geist, Tabs — https://vercel.com/geist/tabs | Tabs solo para vistas hermanas del mismo ámbito; 1–2 palabras, sustantivo; cambio instantáneo; flechas izquierda/derecha y foco visible; no meter contadores en el título. | «Grafo» y «Lista» son dos vistas del mismo conjunto filtrado, con un único tab stop y flechas. Los contadores van en la barra lateral y el resumen, no en los tabs. Las áreas no hermanas siguen siendo navegación lateral. |
| Grafana, Node graph — https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/node-graph/ | Grafo de nodos y aristas con disposición por capas; cada nodo lleva título, subtítulo y estadísticas; aristas con estilo de línea configurable; resaltado de elementos. | Disposición por capas propia: columnas = fase de trabajo (Planificar → Entregar), carriles = instancia. Nodo con título de dos líneas e id monoespaciado; la forma codifica el tipo (píldora agente, rectángulo tarea, rombo decisión). Arista continua = dependencia, discontinua ámbar = espera decisión humana. Al seleccionar, se resaltan los vecinos y se atenúa el resto. |
| Temporal Web UI — https://docs.temporal.io/web-ui | Lista filtrable de ejecuciones por estado, tipo y atributos; detalle con historial, workers y relaciones; vistas guardadas. | Vista Lista equivalente al grafo para teclado y lectores de pantalla. El inspector muestra «Depende de» / «Habilita» con enlaces navegables, como relaciones en lugar de una ficha de estado. |

## Direcciones consideradas

1. **Consola grafito** — modo oscuro, grafo como protagonista a pantalla completa, paneles flotantes. Descartada: imita la estética genérica de observabilidad y empeora la lectura de preguntas largas.
2. **Mesa de despacho** (elegida) — papel cálido claro, tinta casi negra y un solo acento azul tinta. El grafo se lee como un plan de despacho: carriles por instancia, fases en columnas y una línea vertical «AHORA». Debajo, una ruta crítica explica en una frase qué detiene la entrega. Las decisiones humanas se tratan como trabajo de primera clase, no como alertas.

Detalle memorable: la línea «AHORA» junto con la ruta crítica textual, de modo que el grafo responde «¿qué me toca decidir para que avance?».

## Sistema visual

- Tipografía del sistema (Segoe UI Variable / SF Pro / system-ui) y monoespaciada del sistema para identificadores. Cuerpo de 14 px, mínimo de 11 px solo en metadatos monoespaciados del grafo; títulos de 23/18/15 px con tracking negativo.
- Color: neutros cálidos; azul `#2f4fb5` = en curso/selección; ámbar `#a8620a` = espera decisión; rojo `#b3352b` = bloqueada; verde gris `#5d7a5f` = hecho. El estado lleva siempre texto o forma además del color.
- Sin banner amarillo. El aviso de maqueta es persistente pero discreto: píldora en la barra superior, sello en la marca, línea de estado inferior y la etiqueta «simulado»/«local» en cada botón.

## Interacciones locales

- Seleccionar nodos (ratón, Enter/Espacio) o filas de la lista actualiza el inspector y resalta vecinos.
- Filtros por tipo, estado, instancia y búsqueda (tecla `/`) aplicados a ambas vistas; estado vacío con indicación.
- Responder una decisión exige elegir una opción; cambia el estado solo en memoria, puede liberar la tarea bloqueada y añade una entrada «simulado» a la actividad. Avisa de que no se entregó a ningún agente.
- Los botones no implementados (Nueva tarea, Abrir terminal, Reasignar…) muestran un aviso de que son simulados.
- Móvil: menú lateral en cajón, el grafo se desplaza dentro de su panel y se centra en la selección, y el inspector y las decisiones se apilan.

## Comprobaciones realizadas

- Capturas reales con Chrome headless de Windows desde el HTML final: escritorio con ventana de 1440 × 1480 y móvil con viewport real de 375 px (iframe de 375 px y recorte a 375 px, porque la ventana headless no baja de unos 500 px).
- Revisión visual: sin desbordamiento horizontal de página a 375 px; el grafo se desplaza solo dentro de su contenedor.
- No hay recursos externos: sin `<link>`, sin `@import`, sin `src=http` ni `fetch`.

## Límites

- No se ejecutaron auditorías automáticas de contraste ni de accesibilidad (axe/Lighthouse); el foco, los roles y el contraste se revisaron solo a mano.
- Claude revisó el código y sus capturas; Codex añadió pruebas automatizadas de interacción y capturas desde el HTML integrado, descritas debajo.
- Solo cubre la superficie «Red de trabajo»; el resto de áreas de la [matriz](capability-map.md) siguen pendientes de diseño.
- Las imágenes de referencia de Linear se vieron como insumo; no se incluyen en el repositorio.

## Revisión e integración de Codex

Chromium/Playwright a 1440×1000 y 375×812: selección con Enter, grafo/lista, filtros de tipo/estado/instancia, búsqueda vacía, opción obligatoria, respuesta y actividad locales. Cero errores JavaScript y cero solicitudes HTTP externas. La primera prueba móvil reprodujo que el cajón tapaba su propio botón; se añadió un cierre dentro del cajón, Escape y retorno del foco. La repetición pasó. Se eliminó el recorte horizontal global y se comprobó el ancho real de ambas vistas. Las capturas finales se regeneraron con viewport móvil real, sin iframe. [Registro de comprobaciones](mockup-studio-original-evidence.json).

La revisión independiente de Claude fue de lectura, sin ejecutar pruebas; aprobó el alcance del arreglo y pidió precisar estos límites. El cajón es navegación no modal; no se afirma atrapamiento de foco ni conformidad WCAG completa. El juicio de originalidad corresponde al autor y queda pendiente de la revisión visual del usuario; las referencias y su transformación se documentan para que pueda valorarse. El encargo de edición de Claude quedó marcado como bloqueado por hooks ausentes en su worktree aunque entregó los cuatro artefactos; Codex realizó después la integración y la verificación indicadas aquí, sin atribuir al encargo una certificación de workflow.

La inspección de las capturas Linux también reprodujo títulos SVG que rebasaban sus nodos al cambiar la fuente de sistema. Se sustituyó el corte por número de caracteres por medición real del texto y ajuste a dos líneas, con etiqueta completa en el inspector. La repetición a1440/375 comprobó los límites SVG de todos los títulos y regeneró ambas capturas.

## Revisión del grafo (2026-10-22, tras «debe mejorar lo del grafo»)

Se conservan marca, paleta, disposición, inspector, decisiones y actividad. Solo cambia el panel del grafo. Es una **propuesta visual nueva** con datos ficticios: no implica contratos de 017, servicios ni capacidad real de escala.

Qué cambia y por qué (017 FR-003/008/024/026/027, 019 FR-036):

- **Estructura.** Filas = agente, agrupadas en marcos por instancia; columnas = fase. Una tarea o decisión vive en la fila de quien la lleva, así que «ejecuta» se lee por posición y no ocupa aristas. La supervisión es un tronco de delegación a la izquierda, del Planificador (supervisor) a los demás agentes.
- **Cuatro vínculos dirigidos, distinguibles sin color:** delegación (continua, punta abierta), dependencia (continua fina, punta llena), espera de decisión (discontinua, punta con tope) y comunicación (punteada, punta abierta; no crea dependencia). Se eliminaron las aristas sin semántica. Las rutas son ortogonales y están escalonadas a mano para que no se crucen dentro de la red.
- **Estado sin color:** cada nodo lleva un glifo (punto, check, pausa, círculo tachado, aro) y el estado en texto en el subtítulo. La forma indica el tipo: píldora para agente, rectángulo para tarea y rombo para decisión.
- **Ruta crítica.** Trazo negro grueso, nodos con borde de tinta y pasos numerados 1–4. Nunca se atenúa, así que se distingue de la comunicación y de las dependencias laterales. El resumen inferior nombra qué decisiones la retienen. Se puede apagar.
- **Exploración local:** zoom (botones, + / −, Ctrl + rueda), encajar (0), 100 %, centrar la selección (C), arrastre del fondo y minimapa clicable cuando la red no cabe. Las flechas mueven el foco entre nodos con un único tab stop (roving) y encuadran el nodo enfocado; Enter selecciona. Al volver a renderizar se conserva el encuadre, como pide el criterio de 017 de no desplazar al operador.
- **Filtros con indicadores:** los chips de vínculo sirven a la vez de leyenda y muestran visibles/total. Hay una barra «Mostrando X de 14 elementos y Y de 16 vínculos» que nombra lo oculto, y el botón «Mostrar red completa». «Solo rama de …» deja los ascendientes y descendientes de la selección. El estado vacío también se ve dentro del grafo.
- **Inspector:** «Camino de bloqueo» (qué retiene a la selección, encadenado hacia arriba) y vecinos agrupados por tipo y dirección, con acceso a cada vínculo. Seleccionar un vínculo (clic, inspector o lista) muestra tipo, dirección, ámbito, si está en la ruta crítica y la fuente, declarada como ficticia.
- **Lista** equivalente: elementos más una tabla de vínculos, navegable con teclado.

Datos ficticios ajustados para la propuesta: se añade T-207 «Revisión y entrega»; D-15 pasa a retener T-205. Se mantienen 3 decisiones.

Comprobado con Chromium/Playwright de Linux a 1440×1000 y 375×812: 26 comprobaciones por viewport pasan, con 0 errores JS y 0 solicitudes externas. El detalle está en [mockup-studio-original-graph-checks.json](mockup-studio-original-graph-checks.json). Las capturas se regeneraron; la de móvil es de página completa.

Límites: no se ha comprobado en Windows (solo con fuentes Linux, aunque el ajuste mide el texto real). No hay pellizco táctil para zoom (se usan los botones). En móvil el grafo empieza por debajo del primer pantallazo porque la cabecera existente no se tocó. Los vínculos no son tab stops propios: se llega a ellos por el inspector o la lista. Sin auditoría axe ni WCAG. La valoración del diseño queda pendiente del usuario.

### Verificación independiente del grafo por Codex

Codex volvió a ejecutar las26 comprobaciones de navegador en cada viewport (1440×1000 y375×812) desde los archivos copiados al directorio principal. Todas pasaron, sin errores JavaScript ni solicitudes externas. Incluyen14 nodos/16 vínculos, textos dentro de nodos, filtros/restablecimiento/vacío, navegación del grafo, lista equivalente, respuesta local y cierre móvil. Se conserva el alcance de prototipo; estos datos no validan carga real ni contratos backend. El registro JSON distingue esta revisión de la evidencia de la maqueta anterior.

## Escalabilidad de la maqueta (2026-10-22, tras «debe ser escalable»)

Se conserva el diseño aprobado: marca, paleta, tipografía, disposición, nodos, inspector, decisiones y actividad. La red de demostración de 14 elementos sigue siendo la vista inicial y se dibuja igual. Lo nuevo es un selector **Escenario** en la cabecera del grafo: demostración, o escenarios **sintéticos** de 100, 10 000 y 1 000 000 agentes. También se abre con `?escala=100|10k|1m`. Todo lo sintético va marcado como tal. Son datos de maqueta generados en el navegador: **no miden ni prometen la capacidad de ningún backend**.

Principio: ninguna pantalla crea nodos, filas ni arrays proporcionales al total. Se limita lo que se ve, no el tamaño del sistema.

- **Modelo bajo demanda** (`<script id="scale-model">`, sin DOM). Cada agente, conteo o página se calcula a partir de su índice; nunca se materializa la red. La jerarquía de rangos tiene como máximo 40 hijos por nivel y hojas de como máximo 40 agentes. La profundidad crece con n (1 M: red → equipo → subequipo → célula → agente), así que no hay tope de diseño. El estado sigue un patrón determinista con periodo 97 y una variante por equipo; por eso los conteos de cualquier rango son exactos con un coste proporcional al número de equipos, nunca a n, y los hijos suman exactamente al padre.
- **Grafo por ámbitos con detalle progresivo.** Cada nivel pinta como máximo 40 tarjetas con nombre, total, barra apilada por estado y bloqueados/esperas. En la hoja se pintan como máximo 40 agentes. Al abrir un agente se carga su **vecindad** (≤ 12 nodos: supervisor, compañero, tareas, decisión, vínculos y ruta crítica) con el mismo dibujo, inspector, filtros de vínculo y lista que la demostración.
- **Navegación.** Clic o Enter seleccionan, y el inspector muestra el resumen del grupo. Enter otra vez, doble clic o «Abrir» entran en el grupo. Las migas de «Ámbito» y la tecla Retroceso suben de nivel; al subir se vuelve a seleccionar y enfocar el grupo del que se venía, para mantener la selección coherente. Las flechas usan un único tab stop (roving) también en las tarjetas. Encajar, centrar y zoom siguen funcionando.
- **Agrupar por** equipos, proyectos (6 proyectos ficticios; los totales se conservan) o estado. En estado, las páginas son de 40 agentes, con «Anteriores/Siguientes» y el rango «41–80 de N» exacto.
- **Buscar y filtrar sin pintar todo.** El texto filtra solo el nivel visible. `AG-…` o un número, con Enter, abren directamente la vecindad de ese agente y reconstruyen las migas (O(profundidad)). «Requiere atención», «En curso» y «Hecho» filtran las tarjetas por sus conteos. El estado vacío explica cuánto queda oculto.
- **Resto de la página.** Las cifras de cabecera y la cuenta de «Decisiones» del menú salen de conteos exactos del ámbito o la red. Las decisiones pendientes se muestran en ventanas de 3 con paginación, y la respuesta sigue siendo local. La actividad se muestra en ventanas de 8 generadas para el ámbito («Más recientes / Más antiguos»). La lista muestra como máximo 40 filas por nivel, más el paginador.
- **Límites declarados en el código:** `LIMITS = {cards:40, leaf:40, rows:50, feed:8, decisions:3, neighbourhood:12}`. `window.__caosMockupChecks()` devuelve los recuentos del DOM actual y `ok` para la QA en navegador.

Cómo trasladarlo después a la web real (propuesta, sin cambios de contrato en esta entrega). El backend expondría resúmenes por ámbito (conteos por estado y, por cada hijo, total, bloqueados y esperas), páginas con cursor para agentes, decisiones y actividad, una búsqueda por id que devuelva la ruta de ámbito, y una vecindad acotada por agente. La UI conservaría los mismos límites visibles (tarjetas, ventanas y paginadores) y las mismas migas. La demostración de 14 elementos equivale a «vecindad + ruta crítica» de un ámbito pequeño.

### Comprobaciones de escala (ligeras, sin navegador)

- `node specs/019-frontend-studio/mockup-studio-scale-checks.mjs`: **33/33**. Comprueba el modelo puro a 100, 10 000 y 1 000 000, además de tamaños arbitrarios (1, 41, 1 601 y 2 500 001). Cubre totales exactos; que los hijos sean ≤ 40 y sumen al padre en cada nivel; que el conteo coincida con la fuerza bruta en rangos pequeños; que el n-ésimo agente por estado y las páginas sean exactos, incluida la última página; búsqueda; vecindad pequeña y referencialmente íntegra; determinismo; actividad dentro del ámbito; totales por proyecto; respuesta local persistente; y que navegar de la raíz a la hoja a 1 M tarde menos de 50 ms sin recorrer el millón.
- `node specs/019-frontend-studio/mockup-studio-dom-checks.mjs`: **17/17** en jsdom (el de `web/node_modules`; no es un navegador). Comprueba que la demostración queda intacta (14 nodos, D-14, 16 vínculos). A 1 M: raíz con ≤ 40 tarjetas y DOM que no crece con n; clic y selección coherentes; Enter abre; bajada hasta agentes y vecindad con vínculos; migas y Retroceso que re-seleccionan el origen; búsqueda `AG-0500000`; filtro de atención; agrupar por estado con paginación 41–80 y por proyecto con el total conservado; lista ≤ 40, decisiones ≤ 3 y actividad ≤ 8 + paginador; respuesta local; vuelta a la demostración. También recorre 100 y 10k de la raíz al agente, arranca 1 M a 390 px y comprueba que no hay errores de script ni textos `undefined/NaN`.

Pendiente, a cargo del lead y en navegador real: capturas de escritorio y de móvil de los escenarios sintéticos; desbordamiento a 375 px; que el texto quepa en las tarjetas con fuentes reales; foco visible; y una nueva pasada de `mockup-studio-original-graph-checks.json` para la demostración. Las capturas y los JSON de evidencia que ya existen corresponden a la versión previa a esta sección y no se han regenerado.


### Revisión e integración del lead (2026-10-22)

Las comprobaciones anteriores de 17 controles y el navegador pendiente describen el estado recibido del worktree. El lead integró el HTML y los controles en main preservando la historia adicional de este archivo. La primera corrección de cabeceras pasó 18 controles DOM. La revisión Luna encontró un parámetro de URL que aceptaba propiedades heredadas y un listener instalado después de ejecutar scripts; se corrigieron con Object.hasOwn y captura en beforeParse. La prueba nueva falló antes (18/19) y pasó después (19/19).

Se reutilizó Chromium1243 y sus bibliotecas privadas existentes, sin instalaciones ni cambios globales. La primera pasada real detectó desbordamiento móvil de las cifras (396px sobre375px) y texto fuera de las tarjetas agregadas de160px. Se corrigieron en su origen: cifras con wrap, una columna de tarjetas de ancho legible en vistas menores de480px, y migas con una fila propia para evitar texto vertical. El orden SVG coloca las etiquetas con halo después de las aristas y antes de los nodos, manteniendo clics sobre los vínculos.

**Resultado final:** 33 controles de modelo, 19 DOM y **26 controles de navegador** (13 por viewport1440×1000/375×812), cero errores JS y cero peticiones externas. El [JSON de navegador actual](mockup-studio-scale-browser-checks.json) fija el hash del HTML y los resultados de demo, texto dentro de tarjetas, etiquetas, zoom, filtros/lista, raíz1M, teclado hasta vecindad, búsqueda/migas y páginas por estado. El script ejecutado y los logs de rechazo se conservan privados. Capturas nuevas: [demo escritorio](mockup-studio-scalable-demo-1440.png), [demo móvil](mockup-studio-scalable-demo-375.png), [1M escritorio](mockup-studio-scalable-1m-1440.png), [1M móvil](mockup-studio-scalable-1m-375.png). Las PNG/JSON sin el nombre scalable/scale-browser siguen siendo históricas; no se atribuyen al HTML nuevo. El lead inspeccionó las capturas de demo escritorio y1M móvil, y comprobó que el HTML servido en8766 coincide con main.

**Composición: PASS con alcance de maqueta.** Modelo puro→estado de ámbito→SVG/lista/inspector/decisiones/actividad; no imports de aplicación, API, persistencia ni credenciales. Los escenarios sintéticos no generan un array de todos los agentes. El baseline de14 elementos y sus16 vínculos se conserva. La red real, tiempos del backend, carga concurrente y aceptación con lectores de pantalla siguen pendientes de sus contratos. No se ha demostrado capacidad infinita; la agrupación progresiva evita depender de renderizar el total. No se modificó Graphify; el grafo consultado no contiene estos archivos estáticos y los límites se verificaron directamente en fuente. Las pruebas no cambian los límites globales de las specs017/019/020.

La segunda revisión independiente reprodujo un zoom negativo al cambiar agrupación desde Lista: -1% en escritorio y -2% en móvil. frame() estaba midiendo el grafo oculto. Se aplaza el encuadre mientras está oculto y se consume tras dibujar al volver a Grafo; alternar pestañas sin cambiar ámbito conserva la cámara. Los dos controles nuevos por viewport verifican cambio de agrupación, cambio de escala y conservación de zoom. La ejecución final pasa26/26, con el hash actual del HTML.

Revisión final Luna de solo lectura: confirmó en fuente la inicialización y el orden render→encuadre al regresar de Lista, sin hallazgos accionables en ese alcance. Los26 controles de navegador y los19 DOM fueron ejecutados por el lead; no se atribuyen al revisor.
