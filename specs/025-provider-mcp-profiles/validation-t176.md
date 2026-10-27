# T176 · Errores Codex mediante la API gestionada

Aceptación acotada **PASS**, 2026-10-27, fuente `303d3f65befa33362c30a98ac4a5848c5831371a`, CLI0.164.0. El CLI real recibe respuestas HTTP sintéticas en loopback aislado; la API, el terminal interactivo y los recibos nativos son de producción. No usa ni copia el login del operador. [Recibo estructurado](validation-t176.json).

| Caso | Respuestas HTTP nativas | API CAOS | Recibo del hijo | Recorrido gestionado |
| --- | --- | --- | --- | --- |
| auth | 12 | 502 / `error` | `failed` | 34.024 s |
| throttle | 2 | 502 / `error` | `failed` | 27.775 s |
| quota | 2 | 409 / `quota_wait` | `reconcile` | 27.645 s |

El esquema del cuerpo se valida contra el OpenAPI de la API arrancada. Tras el error, el hijo permanece accesible aun con `teardown=true`: se contrasta identidad padre/hijo y el mismo veredicto mediante join antes del DELETE explícito. Después se exige terminal404, sello de limpieza y conservación de estado/settled_at; también salida del proceso wrapper por identidad de nacimiento/pidfd. `reconcile` es un veredicto duradero que satisface join, no una tarea exitosa. No hay redelivery del prompt ni reintento automático del controlador; las repeticiones HTTP internas del CLI mantienen una generación de recibo.

Dos ejecuciones seriales: auth primero, después throttle/quota en un HOME nuevo sin repetir auth. Cada una fija1726 archivos de fuente, Python, binario, lanzador, wrapper y backend realmente ejecutado; rutas privadas y observaciones quedan fuera del repositorio. HOME del operador oculto, solo loopback en el namespace, clave sintética. Los descriptores quedan cerrados; API, HOME temporal, tmux y procesos propios terminan. Cero conexiones/bases temporales antes del cierre del propietario PostgreSQL; contenedor y red exactos ausentes. No se afirma una inspección independiente de desaparición del inode de namespace.

18 contratos nuevos incluyen padre/join ajenos, retryable, falso éxito y limpieza ausente. RED18 antes de implementar; GREEN66 enfocados y regresión172PASS en58,09s, sin omisiones. Arquitectura5conservados/0rotos. No cambia producción en T176: valida la corrección previa T175 contra la composición real. Revisión READ Claude y Luna, con sus alcances detallados en el JSON.

Quedan abiertos errores tras una respuesta previa del asistente, cancelación activa, timeout/reconciliación y el resto de CX-08. No se acepta agotamiento de cuota real, auto-resume, otros proveedores ni gates globales.

Conocimiento duradero actualizado mediante la transacción Obsidian `save-20261027-caos-managed-native-errors`: cuatro páginas, lint estricto sin incidencias.
