# T082 — separación nativa del razonamiento en Mini

2026-10-23. Corrección preparada; aceptación real pendiente. Sigue al timeout documentado de T080, sin repetir aquella fuente sin cambios.

La fuente exacta v2.0.18 distingue entradas assistant y reasoning. El transporte solo emite razonamiento cuando thinking está activado, tanto al hidratar mensajes como al recibir deltas/fin de razonamiento; el texto assistant sigue una ruta distinta. La opción CLI mini.thinking=hide oculta esa presentación, sin desactivar el razonamiento del modelo ni cambiar herramientas/permiso o formato de respuesta.

Fuentes primarias fijadas: [transporte Mini](https://github.com/anomalyco/opencode/blob/v2.0.18/packages/tui/src/mini/stream-v2.transport.ts), [schema TUI/mini](https://github.com/anomalyco/opencode/blob/v2.0.18/packages/tui/src/config/index.tsx), [carga y merge de configuración CLI](https://github.com/anomalyco/opencode/blob/v2.0.18/packages/cli/src/config/config.ts), [resolución de preferencias](https://github.com/anomalyco/opencode/blob/v2.0.18/packages/tui/src/mini/runtime.boot.ts). No se traslada a Mini una suposición tomada de la TUI estándar.

El lanzamiento administrado v2 fija únicamente OPENCODE_CLI_CONFIG_CONTENT con mini.thinking=hide en el proceso hijo. Esa configuración tiene precedencia sobre el archivo; no se escribe configuración del operador ni se lee su DB para recuperar respuestas. El padre conserva su entorno y v1 conserva su lanzamiento. T081 sigue rechazando texto Thinking ambiguo si aun así llega al extractor. No se cambian los knobs de herramientas, footer, resumen, el modelo ni los controles de ACK/recibo.

TDD: la prueba del contrato de lanzamiento reprodujo el override ausente (RED). Comprueba el valor exacto del hijo, precedencia sobre entrada ambiente contraria, preservación del entorno padre y ausencia del override en v1. El replay guardado de T080 permanece fallido; no se transforma su captura en una aceptación posterior.

Composición: mismo lanzador privado y dos consumidores de extracción, sin nuevas dependencias, persistencia o API. Graphify previo contrastado con fuente; el grafo ajeno permanece intacto y no requiere regeneración por este ajuste de entorno. Riesgo residual: soporte probado por fuente de v2.0.18 y por la siguiente ejecución fijada; no es aceptación automática de otras versiones/modelos ni cierre de los gates globales.

Validación offline:219 pruebas enfocadas PASS en130,41s,2warnings; Black/isort/diff-check PASS; composición412archivos/1834dependencias/5contratos conservados/0rotos. Revisión independiente Codex de schema, merge y filtrado tipado: sin hallazgos accionables. El razonamiento permanece en la sesión interna; solo cambia su presentación en todos los Mini v2 administrados. Veredicto de composición offline PASS WITH RISKS hasta ejecutar el recorrido nativo fijado.
