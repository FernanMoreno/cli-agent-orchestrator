# T160 — origen cargado del propietario de ciclo de vida en v2

2026-10-26. FR138/SC122. Corrección de procedencia del lanzador, **validada offline**. [Recibos, fuentes y revisión](validation-t160.json). No es un fallo nativo reproducido ni una aceptación v2 nueva.

El inventario dinámico ya incluía/hashaba provider_lifecycle, ahora importado por terminal_service, pero el mapa exacto de módulos cargados de v2 no lo exigía. Un hash de archivo no prueba desde qué ruta se importó su implementación. RED2fallos/2controles en27,24s: el matcher aceptaba omitir el propietario y rechazaba añadir su ruta canónica; la ruta desviada y el bloque de importación real previo a recursos eran controles positivos.

Se añadió únicamente el nombre del módulo al tuple obligatorio y su ruta canónica al mapa. El bucle existente exige presencia en sys.modules, resuelve __file__, rechaza rutas fuera del repositorio y compara el mapa completo. GREEN4PASS en22,57s, incluido el bloque de importación real con red/procesos/hilos prohibidos. Los controles existentes de omisión/extra/desajuste se conservan; no se cambia el proveedor, SQL, perfiles, habilitación cerrada, criterios nativos ni límites.

Regresión final:134pruebas del fixture en50,07s y18de diagnóstico de respuesta en29,98s: **152PASS**,3avisos. PostgreSQL identificado y retenido durante ambos grupos; configuraciones de runtime/migración separadas. Targets/propietario cerrados y contenedor exacto ausente en90,001s.19fuentes y2archivos cambiados permanecieron iguales; Black/isort/diff pasan. Arquitectura5/0, composición revisada y GO Claude. Graphify readonly201nodos/842aristas contrastado con fuente, sin mutar el catálogo ajeno. Obsidian actualizado por save-20261026-caos-v2-loaded-origin,lint estricto0.

T159 mantiene el PASS v1 acotado sobre runtimeb2b56b67; T157 conserva causa desconocida. Siguiente: preparar un v2 full8 nuevo desde el HEAD aceptado tras T160, binario fijado, inventario dinámico y PostgreSQL propio verificado. No ejecutar descriptores/PG/recibos históricos T135 ni trasladar su aceptación0fe560dc a la fuente actual. OAuth/modelos/concurrencia/global/WorkT143 permanecen separados.
