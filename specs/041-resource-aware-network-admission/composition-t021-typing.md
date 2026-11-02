# T021.T — revisión de composición

**PASS en la corrección de tipos y regresión aplicable.**

- Subsistemas: lector nativo OpenCode v2, contrato de artefactos Work, routing/ghost cleanup Herdr y clasificación de respuesta peer. Vecinos: origen de resultados, reemplazo de owners y caminos status/cancel con transporte fallido.
- Invariantes: mismo esquema de índices y rechazo de índice parcial; mismo protocolo de artefactos; registro/alias/owner exactos; mismo filtro HTTP404, igualdad JSON exacta y ValueError→False. No hay casts, ignores nuevos, límites o conexiones añadidas.
- Tipos: caché nueva, configuración efectiva intacta; src/ **454 archivos PASS** y cono Bridge **2 archivos PASS**. Las bases de ambos alcances daban ocho errores con conjuntos distintos; el último retorno Any solo apareció al ampliar el chequeo. Se conservan los diagnósticos, no se rebaja configuración para obtener verde.
- Pruebas: **129 PASS / 0 fallos, errores u omisiones / 5 advertencias** en cuatro módulos existentes. Lectura Unicode y forma de índices, resultados concurrentes/repetidos/invalidos, registro/reemplazo de pane y diez mil owners completados sin historial, errores peer exactos/ampliados/malformados y outages. Ninguna prueba o aserción se eliminó.
- Persistencia: PostgreSQL 18.6 real para fixtures Work/Herdr y SQLite temporal propio del formato nativo OpenCode para el lector, sin cambiar la persistencia CAOS. Mismo contenedor PostgreSQL verificado; final 0 clientes, 0 bases y 0 roles de prueba. Sin DDL productivo ni estado de jobs históricos.
- Arquitectura: 457 archivos, 2062 dependencias, 5 contratos conservados y 0 rotos. Graphify parcial en snapshots privados contrastados por hash; artefactos ajenos preservados. Revisión GPT-6 Luna Max read sin bloqueos, contrastada por raíz con fuente y checks reales.
- Contratos externos: no cambia forma de HTTP/eventos/socket ni añade consumidor/proveedor; Pact no aplica a estas anotaciones y comparación preservada.
- Conocimiento: se reutiliza la conclusión durable de validación en frío ya guardada en Obsidian; no se duplican código ni resultados rutinarios en otra nota.

Root, driver financiado productivo/custodia, cuotas, M1/M2/M3 y SDK/UI siguen pendientes. T021 global y los diez gates no se cierran por este componente. La regresión Bridge anterior de 1.423 casos conserva su propio archivo ejecutado y no se suma a estos 129.

[Validación](validation-t021-typing.json).
