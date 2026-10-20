# Investigación: recursos y límites del rediseño integral

Consulta: 2026-10-10, Europe/Madrid. Fuentes oficiales y código local. Las cifras sociales son una instantánea aproximada; no son prueba de usabilidad. No se compraron plantillas, publicaron proyectos ni enviaron archivos del repositorio a un generador externo.

## Recursos encontrados y recomendación

| Recurso | Qué aprovechar | Procedencia y reutilización | Encaje en CAOS |
| --- | --- | --- | --- |
| [M.O.N.K.Y — v0](https://v0.app/templates/b7GDYVxuoGC) | Identidad visual contundente, navegación lateral, varias vistas y detalle contextual. Su ficha describe chat y tema oscuro; muestra ~11,1k forks y ~1,2k likes. | Publicado en galería v0 por JOYCO; no acredita autoría exclusiva de IA ni licencia MIT. Verificar condiciones del material antes de copiarlo. | Principal referencia de personalidad; adaptar legibilidad y densidad a sesiones prolongadas. No adoptar métricas o contenido de ejemplo como funciones CAOS. |
| [Tasko — v0](https://v0.app/templates/SGGKgsJdrxG) | Recorridos de gestión de tareas, agrupación de proyectos, navegación y tema oscuro. La ficha declara React 19/Next 16/Tailwind 4 y muestra ~2,3k forks/~420 likes. | Plantilla de comunidad v0; fork en la plataforma no prueba licencia OSS del código y assets. | Referencia de claridad en biblioteca de agentes y tareas. No exige migrar CAOS a Next. |
| [SalesOps — v0](https://v0.app/templates/9q2Mfgu6cDi) | Tablas, filtros, detalle y composición de un dashboard operativo. Ficha con ~2k forks/~397 likes. | Comunidad v0, revisar licencia concreta si se reutiliza código. | Inspiración secundaria; adaptar a estados y acciones de CAOS, sin copiar indicadores de ventas. |
| [COMPUTE — v0](https://v0.app/templates/Auw4otwlr20) | Tipografía y presentación de producto para agentes; ficha ~5,9k forks/~945 likes. | Es una landing page de la galería, no un frontend operativo de orquestación. | Referencia de marca, menor prioridad para resolver operaciones. |
| [shadcn/ui Blocks](https://ui.shadcn.com/blocks) | Sidebar, formularios, tablas, paneles, navegación y temas con código editable. | El proyecto oficial tiene [licencia MIT](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md). Los sitios de terceros con nombres parecidos tienen condiciones propias. | Candidato de base común para el frontend. La página ofrece abrir bloques en v0 para iterar con IA. |
| [React Flow](https://reactflow.dev/examples) y [React Flow UI](https://reactflow.dev/ui) | Lienzo de nodos/aristas, grupos, controles, búsqueda y nodos custom; ejemplos gratuitos MIT y ejemplos Pro separados. | Core y ejemplos públicos según licencia declarada. La biblioteca UI actual apunta a React 19/Tailwind 4. | Candidato para editor y mapa operativo, con adaptadores distintos para diseño y observación. Compatibilidad con React 18/Tailwind 3 actuales necesita decisión de plan. |
| [AI Workflow Editor — React Flow](https://reactflow.dev/ui/templates/ai-workflow-editor) | Referencia directa de editor con nodos, shadcn y Zustand; usa Next y AI SDK. | La página lo identifica expresamente como plantilla Pro. No se ha comprado ni descargado su código. | Referencia funcional; alternativa gratuita: componer editor propio con el core y ejemplos públicos. No aporta los contratos CAOS. |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | Componentes componibles de conversación y formularios/acciones asociados a herramientas; runtime propio disponible. | Repo MIT, servicios cloud opcionales. Su documentación explica los [patrones generativos](https://www.assistant-ui.com/docs/tools/generative-ui). No se ha instalado. | Candidato opcional para conversación/bandeja; adaptar al canal CAOS. Componente de aprobación no acredita entrega ni autorización de 016. |
| [Langflow](https://github.com/langflow-ai/langflow) y [editor visual](https://docs.langflow.org/concepts-overview) | Patrón de biblioteca de componentes, lienzo, configuración, Playground y logs integrados. | Repositorio MIT; es un producto con motor propio. No hay prueba aquí de que toda su interfaz sea diseñada por IA. | Referencia de recorrido completo de agentes/prompts/workflows. Adoptar patrones sin sustituir el motor CAOS ni asumir semántica compatible. |

### Qué significa «diseñados con IA»

La [documentación oficial de v0](https://v0.app/docs) describe un agente que produce interfaces y aplicaciones desde prompts. Sus [templates](https://v0.app/docs/templates) son chats publicados para que otros usuarios hagan fork; la galería destaca proyectos por forks y likes. Esto verifica un ecosistema de creación asistida por IA y permite descubrir diseños con aceptación comunitaria. No revela qué porcentaje de cada plantilla fue escrito o diseñado por humanos. La selección distingue ese origen de bibliotecas OSS y de productos de referencia.

Recomendación de composición: shadcn/ui para estructura común, React Flow para el lienzo, xterm existente para terminales y componentes propios de autoridad/Work/016. assistant-ui es opcional según el contrato de conversación que se elija. M.O.N.K.Y y Tasko orientan la dirección visual, con diseños y código originales adaptados a CAOS. No se instala ni migra nada durante esta fase.

## Inspección visual y límites

Se abrieron con navegador las páginas de M.O.N.K.Y, Tasko, Langflow y assistant-ui. M.O.N.K.Y se inspeccionó además mediante captura de la preview: estructura oscura, navegación lateral, centro de trabajo y columna contextual; tipografía muy expresiva que se suaviza para texto de operación. No se recorrieron ni probaron todas las funciones de esos productos. Avisos de consola de las páginas externas no se atribuyen a CAOS.

No se ha realizado una auditoría visual ejecutando el frontend local. El diagnóstico siguiente es de estructura y fuentes, no un informe inventado de puntuaciones de accesibilidad o rendimiento.

## Hallazgos en código local

- [App.tsx](../../web/src/App.tsx) contiene navegación por pestañas y monta cada panel. Orden de navegación condicionado por atajos; memoria se oculta si el check falla y plugins tienen gate de compilación. Cambio de tab desmonta la vista, por lo que existen locks de navegación para no perder guardados en vuelo.
- [index.css](../../web/src/index.css) fija fondo oscuro y foco global. [package.json](../../web/package.json) usa React 18, Tailwind 3, Zustand, xterm y Sigma/Graphology. La investigación de recursos no justifica por sí sola una actualización coordinada de dependencias.
- [ProfileCreateModal](../../web/src/components/ProfileCreateModal.tsx) ya permite plantilla/configuración/fuente, prompt de sistema, preview y validación. [ProfileEditorModal](../../web/src/components/ProfileEditorModal.tsx) y [API](../../web/src/api.ts) distinguen source de perfil resuelto. El rediseño debe preservar variables y secretos sin round-trip del perfil resuelto.
- [AgentProfile](../../src/cli_agent_orchestrator/models/agent_profile.py) contiene prompt, system_prompt, role, skills, tools, recursos, MCP, modelos y opciones por proveedor. No todos son transferibles entre CLI; usar esquema y descriptor de proveedor y revisar composición efectiva.
- [AgentPanel](../../web/src/components/AgentPanel.tsx) lanza con perfil/proveedor/directorio. En el cliente la creación de sesión y el envío de input son operaciones distintas. Lanzamiento con tarea inicial guiada necesita una frontera de entrega que evite duplicar efectos tras inicialización/timeout.
- [API main](../../src/cli_agent_orchestrator/api/main.py) permite CRUD/source/validate de workflows, preparar/revisar/aprobar planes, submit, cancel, resume, retry/replay, outputs/result, comparación y diagnóstico. [WorkflowPlanReview](../../web/src/components/workflow/WorkflowPlanReview.tsx) y [RunDetail](../../web/src/components/workflow/RunDetail.tsx) cubren parte del recorrido; no se encontró un editor visual completo integrado.
- [Workflow model](../../src/cli_agent_orchestrator/models/workflow.py), [constants](../../src/cli_agent_orchestrator/constants.py) y [workflow_service](../../src/cli_agent_orchestrator/services/workflow_service.py) mantienen reservas explícitas: WORKFLOW_SHIPPED_UNITS vacío y seams parallel/pipeline/loop que lanzan NotBuiltYetError. Aunque se puedan dibujar conexiones, no se debe prometer que todo DAG/loop se ejecuta.
- [Beads routes](../../src/cli_agent_orchestrator/api/beads_routes.py), [Ralph routes](../../src/cli_agent_orchestrator/api/work_coordinator_routes.py), [Work routes](../../src/cli_agent_orchestrator/api/work_routes.py) y [knowledge routes](../../src/cli_agent_orchestrator/api/knowledge_routes.py) tienen semánticas/políticas distintas: aprobación de plan, progreso Work, autorización por recurso, decisiones, conocimiento versionado y recovery no son un solo CRUD genérico.
- [Browser auth](../../src/cli_agent_orchestrator/api/browser_auth_routes.py) y [web auth](../../web/src/auth.ts) son parte del producto: setup, login, renovación, revocación y sesión. Tema nuevo no elimina estos límites.
- [CLI commands](../../src/cli_agent_orchestrator/cli/commands) ofrece capacidades más amplias de entorno, configuración, memoria/vault, instalación, skills y coordinación. Cobertura integral exige revisar estos recorridos y planificar APIs humanas donde falten; una terminal de administración genérica no satisface paridad.
- [Fleet panel](../../examples/fleet/panel/app/main.py) es un ejemplo coordinador y filtra namespaces de proxy. Nuevas áreas de conocimiento/decisiones/runtime no se presumen alcanzables por este proxy; habrá que verificar rutas, credenciales y autorización de extremo a extremo.
- El [grafo de memoria existente](../../web/src/components/MemoryGraphView.tsx) usa otra semántica. Mantener área de conocimiento independiente del mapa de ejecución 017 y del editor de workflow.

Graphify: `graphify explain web/src/App.tsx` identificó los módulos UI/API/store/auth revisados. Sus relaciones se contrastaron contra importaciones actuales; el grafo estructural no es la visualización de ejecución solicitada. No se regeneró al no cambiar estructuras de producto.

## Fronteras para la planificación

1. Inventario vivo UI/API/CLI/MCP → matriz de capacidades → recorridos → evidencias. El inventario estático adjunto es un baseline de declaraciones, no certificación de cobertura runtime ni descubrimiento de todas las rutas dinámicas.
2. Shell/navegación/borradores → permisos, identidad y cambios de ámbito. URL, búsquedas y preferencias no conceden autoridad; los borradores con secretos necesitan política propia.
3. Perfil fuente → composición efectiva del proveedor → arranque → tarea inicial. Guardar, instalar, lanzar, entregar y obtener resultado son efectos diferentes.
4. Editor visual ↔ fuente → validación del motor → preparación/aprobación → submit. Preservar campos desconocidos y limitar construcciones ejecutables; invalidar plan ante cambios.
5. UI → Work/Beads/Ralph/workflow: mantener estados/identidades, decisiones, cancelación y limpieza, reconciliación y efectos protegidos. No usar raw input como bypass.
6. UI → interacciones 016 → entrega al CLI → seguimiento 017/018. Las piezas de presentación no resuelven por sí solas un diálogo ni verifican su continuación.
7. Conocimiento/vault y plugins/skills → políticas, migraciones y compatibilidad. Distinguir legacy de autoridad versionada, importación de promoción y configuración de efecto en proceso activo.
8. Adaptadores de origen/Fleet/proxy → contratos de lectura y escritura autorizados. Capabilities, versiones, destino cualificado, credenciales y recursos de seguimiento separados por instancia.
9. Tokens/componentes → compatibilidad de stack → recursos locales/font/licencias. Migrar React/Tailwind o adoptar librería es una decisión de plan, no efecto implícito de esta investigación.
10. Observación → rendimiento/virtualización/accesibilidad → prueba de recorridos. Medir con datos y usuarios representativos, no con contadores falsos o popularidad de plantillas.

## Composition Review Report

- **Changed subsystems**: documentación, matriz, inventario estático, tokens y prototipo aislado en spec 019; selector Spec Kit. Ningún módulo de producción modificado.
- **Neighbors reviewed**: shell web, store/auth/API, perfiles y lanzamiento, workflows, Beads, Ralph, Work, conocimiento, memoria, CLI y Fleet; specs 016–018.
- **Contracts/invariants checked**: lectura de fuentes sobre autoridad, borradores/fuente, reserva de modos, destino, estado durable/proceso, raw input y gates.
- **Architecture checks run**: no ejecutados por ausencia de cambios de implementación. Se exigirán gate de composición y comprobaciones web/backend aplicables cuando se implemente.
- **Composition/integration scenarios run**: ninguno de producto. Los escenarios del spec son aceptación futura. El prototipo es navegación/datos simulados, sin conexiones CAOS.
- **Contract tests / real dependencies**: no aplican a esta entrega de diseño; requieren backend y CLI/instancias reales en implementación.
- **Failures/root causes**: no se corrigen defectos en esta fase. Brechas comprobadas: editor visual completo no localizado, modos de workflow reservados y cobertura CLI/proxy mayor o distinta a vistas actuales.
- **Residual risks**: cobertura final debe reconciliar inventario actualizado y contratos nuevos; licencias/versiones concretas de recursos no adoptados deben confirmarse al elegirlos. No existe aprobación de estilo definitiva ni validación con operadores aún.
- **Verdict**: PASS WITH RISKS para investigación y propuesta, sujeto a verificaciones documentales; no certifica frontend integrado.
