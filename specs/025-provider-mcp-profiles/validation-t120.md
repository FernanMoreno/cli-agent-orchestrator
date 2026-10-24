# T120 — resultado lógico nativo v2 frente a presentación

Estado: implementación en curso en worktree aislado; no aceptación corregida ni cierre de T077/T116.

El genérico R2 en4c243f6b/517 falló por LAST Unicode. Su diagnóstico T119 observó una única respuesta nativa actual, agente esperado y completada: texto público15 caracteres/una línea, literal exacto; salida extraída15 caracteres/dos líneas, literal ausente. El receptor sí acreditó ACK exacto. [Recibo resumido](validation-t116-r2-native.json).

El replay de fuente sobre la captura privada enlazó un único recibo por digest en memoria y reprodujo exactamente la forma fallida; no volvió a comprobar PG. El marcador completo aparece solo en el eco del usuario. En el cuerpo del asistente las dos filas ya están separadas; el recorte de sidebar no cambia la captura y el extractor conserva ese salto. T083 había observado normal/-J/normal sin recuperación de soft-wrap; no autoriza cambiar tmux para unir filas. La evidencia localiza una diferencia posterior al almacenamiento, sin observar HTTP bruto ni demostrar una causa interna concreta de Mini.

Plan de corrección: hook de proveedor tras witness TUI y antes del hash/CAS durable. Solo v2 con recibo activo y estado normal COMPLETED; fallo del lector deja el turno pendiente, sin fallback ni publicación de estado. Fuente nativa exclusivamente desde root propio activo, con agente vinculado al lanzamiento y recuperable tras reinicio, usuario actual/nonce único y respuesta completada/agente/recibo exactos. Mantener lectura readonly acotada y todos los controles de T119. Conservar los saltos semánticos del texto nativo y el CAS actual. Las lecturas de resultado ya verificado usarán el texto durable y no reabrirán SQLite.

SpecFR099/SC083, plan y tarea preceden la implementación. Graphify devuelve proveedor/base/manager/servicio, con líneas obsoletas y fixtures nuevos ausentes; la ruta se verificó contra fuente. No se reconstruye el grafo ajeno. La consulta actualizó únicamente su sello de consulta del caché, que se deja fuera del commit; el contenido ajeno del grafo no forma parte de este trabajo.

Revisiones de diseño: Luna Max y Claude read convergen en el hook tras witness/pre-CAS. Claude retiró expresamente su atribución inicial al modelo: solo hay TEXT almacenado, no wire HTTP. Pendientes RED/GREEN, revisión exacta de implementación, composición e integración y después una ejecución genérica nueva ligada al commit.
