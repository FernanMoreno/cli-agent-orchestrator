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
