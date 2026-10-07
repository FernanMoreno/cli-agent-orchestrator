# Feature Specification: Fiabilidad y validación de workflows

**Feature Branch**: `012-workflow-reliability` (no se creó rama; especificación documental)<br>
**Created**: 2026-10-03<br>
**Updated**: 2026-10-04<br>
**Status**: Draft<br>
**Input**: Hallazgos de auditoría sobre reintentos, equidad de continuaciones, ciclo de vida de tareas, identidad/configuración de workflows y sesiones de instancias CAO locales.

## User Scenarios & Testing

### User Story 1 — Avanzar el trabajo pendiente con equidad (P1)

El operador ve que las ejecuciones listas avanzan aunque existan otras ejecuciones pausadas o bloqueadas, y que una caída temporal de almacenamiento no dispara reintentos sin control.

**Why this priority**: Una ejecución elegible que queda fuera del lote o un loop de reintento puede degradar todo el servicio.

**Independent Test**: Crear una cola mayor que una ronda normal de procesamiento, mezclar estados elegibles/no elegibles y simular fallos y recuperación del almacenamiento.

**Acceptance Scenarios**:

1. **Given** una cola de ejecuciones elegibles mayor que una ronda normal, **When** el servicio procesa rondas sucesivas, **Then** cada ejecución elegible progresa sin quedar postergada por entradas antiguas no elegibles.
2. **Given** ejecuciones pausadas y listas mezcladas, **When** se buscan continuaciones, **Then** solo las elegibles ocupan capacidad de avance.
3. **Given** una falla al leer el estado, **When** el servicio reintenta, **Then** aplica una espera acotada que aumenta durante fallos consecutivos y se normaliza al recuperarse.

### User Story 2 — Liberar recursos al terminar una ejecución (P2)

El servicio puede procesar muchas ejecuciones durante su vida sin retener indefinidamente recursos asociados a tareas terminadas.

**Why this priority**: La retención acumulada puede convertir una operación estable en consumo creciente de memoria.

**Independent Test**: Completar y fallar ejecuciones repetidamente; verificar que el registro de tareas activas refleja solo trabajo en curso y que los resultados necesarios siguen disponibles por su canal durable.

**Acceptance Scenarios**:

1. **Given** una tarea que termina con éxito o error, **When** el servicio la observa como terminada, **Then** la retira del conjunto de tareas activas.
2. **Given** una nueva tarea para la misma ejecución, **When** termina una tarea antigua, **Then** la limpieza antigua no elimina el registro de la tarea nueva.

### User Story 3 — Mantener identidad y configuración verificables (P1)

La persona que define un workflow recibe un error claro ante campos no reconocidos, y las opciones de usuario no pueden reemplazar la identidad que el sistema asignó a un paso.

**Why this priority**: Typos de configuración pueden apagar validaciones; identidad sobrescrita puede confundir registro, replay y recuperación.

**Independent Test**: Enviar configuración correcta, campos desconocidos y opciones que intentan reemplazar valores reservados; comprobar validación y correlación de cada paso.

**Acceptance Scenarios**:

1. **Given** un campo desconocido en un workflow, **When** se valida el documento, **Then** la persona recibe diagnóstico con ruta del campo antes de ejecutar.
2. **Given** una opción que intenta reemplazar la identidad del paso, **When** se prepara la ejecución, **Then** se conservan los valores de identidad asignados por el sistema.
3. **Given** una configuración válida con extensiones compatibles, **When** se valida, **Then** no se rechazan campos expresamente reconocidos.

### User Story 4 — Consultar sesiones de otra instancia CAO local (P2)

El usuario puede consultar sesiones de otra instancia CAO que se ejecuta en la misma PC y está autorizada para el proyecto. Si una sesión o el proceso desaparecen durante la consulta, CAO presenta un resultado coherente en vez de un error interno.

**Why this priority**: La coordinación entre instancias locales debe seguir siendo manejable cuando cambian las terminales o se detiene otro proceso CAO.

**Independent Test**: Enumerar sesiones de otra instancia CAO local mientras se elimina una sesión y mientras se detiene ese proceso; comprobar respuestas válidas y estados claros.

**Acceptance Scenarios**:

1. **Given** una sesión eliminada durante su enumeración, **When** CAO termina de responder, **Then** la omite o devuelve un snapshot coherente sin error interno.
2. **Given** otro proceso CAO autorizado que se detiene, **When** el usuario consulta sus sesiones, **Then** recibe un estado de proceso no disponible y la vista de su instancia sigue funcionando.

### Edge Cases

- Una tarea termina justo cuando se registra una tarea sucesora para la misma ejecución.
- El almacenamiento falla repetidamente y luego se recupera durante un lote parcial.
- Una ejecución cambia de elegible a pausada entre selección y despacho.
- Un workflow contiene campos de versiones antiguas que antes se ignoraban.
- Se elimina una sesión de otra instancia local mientras se prepara la respuesta.
- Se detiene o reinicia el proceso CAO local consultado durante una lectura de sesiones.
- Resultado, cancelación y cleanup ocurren casi al mismo tiempo.

## Requirements

### Functional Requirements

- **FR-001**: El sistema MUST hacer progresar todas las ejecuciones elegibles sin que estados no elegibles consuman su capacidad de forma permanente.
- **FR-002**: El sistema MUST aplicar reintentos limitados y pausas crecientes ante fallos repetidos de almacenamiento u observación.
- **FR-003**: El sistema MUST retirar tareas terminadas del conjunto de tareas activas sin borrar reemplazos más recientes.
- **FR-004**: El sistema MUST conservar la relación entre cada paso, su ejecución, generación e identidad asignada por el sistema.
- **FR-005**: El sistema MUST impedir que opciones de usuario sobrescriban campos reservados de identidad.
- **FR-006**: El sistema MUST identificar campos de configuración desconocidos antes de iniciar una ejecución, con diagnóstico accionable.
- **FR-007**: El sistema MUST definir compatibilidad explícita para extensiones de configuración admitidas.
- **FR-008**: El sistema MUST conservar resultados y evidencia necesarios después de limpiar recursos de ejecución.
- **FR-009**: El sistema MUST exponer suficiente estado para distinguir espera normal, reintento, pausa, fallo y finalización.
- **FR-010**: El sistema MUST tolerar la desaparición concurrente de una sesión en otra instancia CAO local sin producir un error interno ni una respuesta inválida.
- **FR-011**: El sistema MUST distinguir entre una lista vacía y otra instancia CAO local que no está disponible.

### Key Entities

- **Ejecución elegible**: workflow que puede continuar según su estado y dependencias.
- **Tarea activa**: trabajo en curso ligado a una ejecución y generación.
- **Identidad de paso**: correlación administrada de run, generación y paso.
- **Diagnóstico de configuración**: ubicación y explicación de un campo inválido o desconocido.
- **Snapshot de sesiones locales**: vista coherente de las sesiones disponibles en otra instancia CAO que se ejecuta en la misma PC.

## Success Criteria

### Measurable Outcomes

- **SC-001**: El 100% de las ejecuciones elegibles avanza en rondas de procesamiento sucesivas bajo carga superior al límite de una lectura.
- **SC-002**: Durante una falla sostenida de almacenamiento, la frecuencia de reintentos se mantiene dentro del límite definido y no crece sin control.
- **SC-003**: Al completar miles de ejecuciones en una prueba de ciclo de vida, el conjunto de tareas activas no conserva tareas completadas.
- **SC-004**: El 100% de los campos desconocidos se informa antes de ejecutar el workflow.
- **SC-005**: En todas las pruebas de sobrescritura, la identidad administrada del paso se conserva.
- **SC-006**: Las consultas de sesiones de otra instancia CAO local nunca generan errores internos cuando una sesión desaparece durante la lectura.
- **SC-007**: Cuando otra instancia CAO local no está disponible, el usuario ve ese estado y conserva el acceso a su instancia.

## Assumptions

- Las configuraciones de workflows pueden evolucionar; compatibilidad y diagnóstico deben ser explícitos.
- El servicio procesa ejecuciones en paralelo y la eliminación concurrente de recursos es esperable.
- Los detalles de paginación, backoff y almacenamiento quedan para el plan, siempre que se satisfagan resultados medibles.
- La auditoría durable de resultados no debe depender de que la tarea async permanezca en memoria.
- El alcance actual es una sola PC: incluye agentes trabajadores de un coordinador (spec 006) y procesos de instancias CAO independientes (spec 009).
- Las consultas de sesiones de otra instancia solo cubren procesos locales autorizados. La coordinación con CAO en otras PCs queda fuera de alcance.
