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
