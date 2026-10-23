# Validación de la entrega documental

Fecha: 2026-10-23. **PASS documental; aceptación de producto pendiente.** [Recibo estructurado](validation.json).

## Alcance y resultado

Se crearon 15 specs individuales (026–040), sus planes iniciales, tareas, evidencia de fuente y checklists: 158 requisitos funcionales y 308 tareas de implementación pendientes. El inventario enlaza 129 detalles semánticos a requisitos y fuentes; registra además 30 controles de marcado estático y 88 funciones nombradas del HTML. No se afirma que contar funciones pruebe todos sus caminos de ejecución.

La comprobación estática final ejecutó 1.288 aserciones con resultado satisfactorio: archivos requeridos y secciones, identidad de requisitos y criterios, tareas sin cierres ficticios, enlaces relativos existentes, dependencias sin ciclos, inventario sin IDs duplicados y enlaces a FR válidos, 27 anclas de fuente y hashes actuales, preservación de los 12 archivos Graphify previamente modificados y ausencia de cambios de producto en src/web/test. Son aserciones documentales, no 1.288 pruebas de producto. El verificador privado utilizado es `/tmp/caos-verify-studio-specs.py`; las entradas persistentes son los documentos y JSON de este directorio.

La primera ejecución de enlaces detectó este informe aún no creado; se corrigió la omisión antes de repetir la comprobación completa. No se cuenta esa primera ejecución como aprobada.

## Revisión independiente

Codex Luna revisó maqueta, fuentes y límites de contratos; después revisó las specs nuevas. Señaló cardinalidad organizativa arbitraria, riesgo de duplicar autoridad y motor de workflows, propiedad del catálogo y trazabilidad FR→tarea. Las correcciones:

- Cardinalidad declarada por la fuente, pertenencias múltiples deduplicadas y conservación de Terminal.group como agrupación opaca hasta adopción explícita.
- Identidad compartida entre instancias solo mediante mapeo y permisos confirmados.
- 028 referencia concesiones y servicios existentes; no crea otra autoridad por equipo.
- 039 proyecta e invoca los motores existentes; 020 es requisito de entrega del recorrido Spec Kit como producto.
- 026 extiende la matriz de 019 en lugar de sustituirla.
- Cada FR tiene tarea individual y evidencia requerida, además de contratos y oráculos detallados obligatorios antes de implementar.
- 040 hereda accesibilidad global de 019 y concreta recorridos del grafo.

La revisión posterior no dejó bloqueos documentales en esos hallazgos. Las últimas precisiones de reasignación, acceso al terminal y unidades de contadores se comprobaron por lectura y validación estática del lead; no se atribuyen a una revisión posterior de Luna.

Claude no pudo revisar: el job `78d46f9e219c4fb98dfa45e0b76ff16d` falló por límite de sesión. No se cuenta como revisión aprobada ni se reintentó autenticación.

## Composition Review Report

- Subsistemas cambiados: solo specs/documentación; ningún servicio, esquema ni comportamiento de producto.
- Vecinos revisados: organización/grupos de terminal, asignaciones e hijos, Work y su proyección, inbox/Work messages, preguntas, journal/eventos, fleet, grafo de conocimiento y navegación Studio.
- Invariantes: fuente de verdad en origen; identidad y encarnación; límites de autorización; separación de proceso/turno/tarea/resultado; cobertura, revisiones, idempotencia y recuperación exigidas a las futuras entregas.
- Arquitectura ejecutada: `project-composition-check "$(cat .ai/project-name)"` → 412 archivos, 1.834 dependencias; cinco contratos conservados, cero rotos.
- Escenarios y contratos: revisión estática de escenarios y propiedad; no se ejecutaron historias de producto porque no se implementó nuevo comportamiento. No hacen falta bases o modelos reales para comprobar enlaces documentales.
- Riesgos: planes iniciales con contratos detallados y presupuestos aún pendientes en T002; aceptación real y cobertura de producto abiertas. El inbox sigue entregando cuerpos al navegador y eso no satisface el futuro contrato ambiental de metadatos; no se presenta como corregido.
- Graphify: mapa anterior a HEAD usado para impacto y contrastado con código; sin refresco por ausencia de cambios estructurales y para preservar trabajo ajeno.
- Conocimiento durable: las decisiones y límites se conservan en estas specs; no se duplicaron en otro vault ni se introdujeron notas de otro proyecto.
- Veredicto: **PASS para esta entrega documental; producto pendiente**.

Los cambios previos de 025, su historial y sus gates se preservaron. No se ejecutaron nuevos modelos, pruebas intensivas, cargas masivas ni operaciones de PostgreSQL, y no se modificaron login o credenciales. Se conservaron los umbrales existentes de 017 en 030/034 y en el índice.
