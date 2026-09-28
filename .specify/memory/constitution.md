<!--
Sync Impact Report
Version: plantilla sin versión -> 1.0.0
Los cinco principios materializan reglas existentes de AGENTS.md y AI_WORKFLOW.md.
Se añaden restricciones operativas y orden de desarrollo; se eliminan marcadores.
Las plantillas dependientes no cambian: leen esta constitución en ejecución.
Pendientes de sincronización: ninguno.
-->
# CAOS Constitution

## Core Principles

### I. Evidencia antes de conclusiones

El código es la fuente de verdad de la implementación. Las conclusiones relevantes deben
contrastarse con código y comprobaciones ejecutables. Graphify aporta relaciones estructurales,
pero no sustituye esa verificación. Una capacidad especificada no se presenta como implementada.

### II. Preservación del trabajo y del alcance

Todo cambio comienza inspeccionando `git status`. Se conservan los cambios preexistentes.
No se realizan commits, pushes, reescrituras de historial, merges remotos, publicaciones,
releases ni eliminaciones remotas sin petición explícita del usuario.

### III. Diseño y diagnóstico antes de implementación

Los cambios significativos requieren Spec Kit: `spec.md`, `plan.md`, `tasks.md`, seguidos
de impacto con Graphify, inspección de código y decisión de TDD. Los fallos requieren
reproducción y diagnóstico de la primera frontera incorrecta antes de modificar comportamiento.
Las exclusiones del flujo necesitan una razón concreta.

### IV. Verificación de composición

Los cambios significativos deben verificar contratos, persistencia, transacciones,
concurrencia, orden de eventos, idempotencia, cancelación, recuperación y compatibilidad
según su superficie real. Se ejecutan pruebas aplicables y gate de composición.
Un test aislado no acredita una garantía que atraviesa varios subsistemas.

### V. Cierre honesto y conocimiento durable

Antes del cierre se revisa el diff y se ejecuta verificación fresca. Los fallos, omisiones
y límites se informan sin declararlos aprobados. Obsidian conserva sólo decisiones,
restricciones y conclusiones durables; no duplica código, Graphify ni registros temporales.

## Restricciones operativas

Las fuentes de estas reglas son `AGENTS.md` y `AI_WORKFLOW.md`. No cambia su precedencia.
Los contratos del producto se especifican en las features correspondientes. No se incorporan
reglas de arquitectura basadas únicamente en nombres de carpetas ni dependencias nuevas
para satisfacer un checker. Las pruebas reales declaran prerrequisitos y casos omitidos.

## Orden de desarrollo

Se sigue `AI_WORKFLOW.md`, secciones 3 y 11. Cada modificación de implementación invalida
la evidencia anterior de cierre. Para cambios significativos: pruebas, composición,
revisión de composición, diff, decisión de actualización de Graphify, decisión de
persistencia de conocimiento y verificación final.

## Governance

Esta versión materializa reglas ya presentes en el repositorio. Las instrucciones explícitas
del usuario prevalecen. Las enmiendas indican motivo, impacto y versión: major para cambios
incompatibles, minor para principios nuevos y patch para aclaraciones. Cada plan verifica
estos principios antes de implementar y antes de cerrar.

**Version**: 1.0.0 | **Ratified**: 2026-09-22 | **Last Amended**: 2026-09-22
