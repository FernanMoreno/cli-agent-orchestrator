# CAOS Studio: dirección visual y de interacción

Propuesta 2026-10-10. Estilo provisional: estudio operativo con temas claro y oscuro. No es un cambio del frontend de producción. [Preview interactiva](design-preview.html) con datos ficticios, sin backend.

## Estructura

Navegación lateral agrupada por trabajo, biblioteca y operación; contexto de proyecto/instancias y búsqueda en cabecera; área central amplia; inspector lateral; terminal/historial inferior bajo demanda. Inicio prioriza preguntas pendientes, trabajo activo y siguiente acción. Paneles y tablas comparten estados y vocabulario.

Las áreas completas están en la [matriz](capability-map.md). No se reduce el producto a un chat ni se exige el terminal para acciones humanas ya soportadas. Las opciones avanzadas se revelan dentro de su contexto.

## Recorridos esenciales

1. Crear agente → plantilla/perfil → instrucciones reutilizables → proveedor/herramientas/permisos → revisar → lanzar con tarea inicial → detalle de arranque/entrega.
2. Crear workflow → plantilla/fuente → pasos/inputs → validar → preparar/revisar/aprobar → enviar → ejecución en grafo/timeline → salidas/recuperación.
3. Pregunta pendiente → tarea/instancia/destinatario → respuesta autorizada → estado de entrega → continuidad observada.
4. Tarea → dependencias/plan → asignación → Work/intento → agente → resultado/decisión/limpieza.

## Tokens propuestos

Los valores están en [design-tokens.json](design-tokens.json). Oscuro: grafito, superficies escalonadas, texto marfil y acento verde lima moderado. Claro: fondo gris frío, superficie blanca, texto casi negro y acento verde profundo. Los estados usan texto/iconos además de color. No se emplea el acento de marca para cualquier resultado positivo.

Tipografía de interfaz: sistema sans para etiquetas y párrafos; monoespaciada de sistema para identidad/código/terminal. Titulares con peso y espaciado propios; evitar la tipografía condensada de una referencia en campos de operación extensos. No se necesitan fuentes externas para el prototipo.

Escala de espacio de 4 px, controles cómodos, radios moderados y borde sutil; sombra para superposición, no para todos los contenedores. Densidad cómoda por defecto y compacta opcional. Color/foco/contraste se medirán en implementación; no se afirma cumplimiento a partir de estos tokens.

Movimiento breve para cambio de panel/estado, nunca desplazamiento continuo de nodos al recibir eventos. Movimiento reducido elimina efectos innecesarios. Zoom y paneo del grafo no sustituyen lista navegable por teclado.

## Estados compartidos

Cargando; vacío con siguiente paso; no configurado; limitado por permiso; capacidad desactivada; fallo; desconectado/desactualizado; aceptado/en curso; completado; incierto/reconciliación. Son estados diferentes. Guardar no equivale a aplicar; entregar no equivale a completar; aprobar no equivale a ejecutar.

## Referencias y adaptación

La [investigación](research.md) recoge URLs y límites de reutilización. M.O.N.K.Y inspira personalidad y composición; Tasko claridad de recorridos; Langflow la relación biblioteca/editor/detalle; shadcn los componentes comunes. Se crea una composición original ajustada a contratos CAOS. React Flow y assistant-ui se evalúan en plan con versiones, accesibilidad y licencias concretas.

## Prototipo entregado

Permite recorrer las áreas propuestas, ver un grafo ficticio, seleccionar agentes, editar instrucciones de un perfil ficticio, configurar un workflow secuencial de ejemplo y alternar temas. El resto de áreas muestra su lista de funciones y brechas reales de integración. Todos los botones de simulación indican que no ejecutan trabajo real. No hay guardado durable, API, terminal real ni permisos verificados en el prototipo.

No sustituye evaluación con usuarios ni cobertura de la matriz. El rediseño completo necesita plan y tareas sobre todas las áreas, no solo las visibles en la maqueta.

## Maqueta de red multiagente · 2026-10-22

[Ver maqueta interactiva](mockup-red-multiagente.html) · [Escritorio](mockup-red-multiagente.png) · [Móvil](mockup-red-multiagente-movil.png). Elaborada por Claude Code en un worktree separado y revisada por Codex antes de integrar estos archivos.

Representa un supervisor Claude y trabajadores Codex/OpenCode, sus relaciones, estados, perfiles MCP ficticios, preguntas y ejecuciones. Permite alternar grafo/lista y tema, seleccionar agentes y consultar preguntas. Es un HTML autónomo que puede abrirse directamente; no necesita servicios ni credenciales.

La indicación «Maqueta · datos simulados» permanece visible. Las respuestas y terminales solo muestran una simulación local; los nombres de herramientas, perfiles y estados no acreditan implementación ni autorización real. No cierra los gates de las specs 017, 018 o 019.

Validación local de Codex: Chromium/Playwright, escritorio de 1440 px y móvil de 375 px; selección de agente y pregunta, acción simulada, cambio de tema y grafo/lista correctos, sin errores JavaScript ni solicitudes HTTP externas. Se corrigió el desbordamiento de la tabla móvil con desplazamiento dentro de su panel. Capturas regeneradas desde el HTML integrado. Esta comprobación del prototipo no sustituye las pruebas de integración del frontend real.

Revisión independiente de Claude Code en modo lectura: sin bloqueos para la entrega visual. Se atendieron sus observaciones de contraste del contador en tema claro, selección de filas con Espacio y estado accesible del botón de tema. En móvil esta maqueta concentra la navegación en Trabajo y oculta Biblioteca/Operación; la preview general sigue enlazada.
