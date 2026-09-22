# Hoja de ruta: orquestación de trabajo verificable

## Objetivo

El salto importante es convertir CAO de un **orquestador de terminales** a un
**orquestador de trabajo verificable**. CAO ya reúne buenas piezas, pero aún
infiere demasiado estado a partir de la TUI.

## Prioridades

| Prioridad | Mejora | Motivo |
| --- | --- | --- |
| P0 | Separar estado de terminal, turno, tarea y job | Un terminal puede estar `IDLE` o `COMPLETED` mientras su tarea o su padre siguen activos. Esto causa estados y colores incoherentes. |
| P0 | Crear recibos durables por tarea: `planned → sent → acknowledged → running → finished/failed/reconcile` | `prompt_redelivery=false` evita duplicar prompts, pero un booleano no sustituye una prueba de recepción y resultado. |
| P0 | Scheduler con allowlist real por job | El job debe admitir sólo los proveedores elegidos al crearlo, validar binario, modelo y esfuerzo disponibles, y nunca caer implícitamente a Grok u otro proveedor. |
| P0 | Árbol nativo de hijos con leases, join y limpieza | `assign` aún depende en parte de que el hijo siga las instrucciones y devuelva el mensaje. Un hijo que muere puede parecer simplemente lento. Debe existir una tarea hija persistida, con padre, lease, resultado y limpieza garantizada. |
| P1 | Evidencia real de uso de herramientas | Pedir “usa Graphify y Specs” en un prompt no lo demuestra. El job debería exigir recibos: herramienta invocada, artefacto creado, hash, ruta y verificación antes de marcar una tarea como terminada. |
| P1 | Una sola proyección de estado para web y API | Los colores ya parten de tokens comunes en CAO, pero la consola AIPM debe consumir el mismo DTO semántico. Fondo, texto, etiqueta y contador deben derivar de una misma causa: `task_state`, no de inferencias distintas. |
| P1 | Pruebas de integración reales de los proveedores | Matriz manual controlada de Codex, Claude y OpenCode: arranque, prompt, hijo cruzado, mensaje entre hermanos, cancelación y reconciliación. Usa procesos autenticados reales y recibos durables; se ejecuta sólo en un runner protegido para no convertir la CI ordinaria en consumo implícito de modelos. |
| P2 | Dividir los módulos gigantes | `api/main.py` tiene aproximadamente 7.960 líneas y `terminal_service.py` aproximadamente 2.956. Extraer rutas por dominio y un reducer de ciclo de vida facilitaría corregir estados sin crear regresiones laterales. |

## Contrato de ciclo de vida propuesto

```yaml
terminal: alive | dead
turn: ready | input_sent | acknowledged | processing | blocked
task: queued | running | waiting_children | succeeded | failed | reconcile
job: planning | running | waiting | completed | failed | revoked
```

La interfaz debe mostrar **“Trabajando”** si la tarea está `running`, aunque
la TUI del terminal parezca momentáneamente `idle`. Sólo debe mostrar
**“Completado”** cuando exista un resultado duradero y validado.

## Estado del fork

La rama `aipm-native-governance` partió de una revisión validada de CAO y, al
momento de redactar este documento, está seis commits por detrás de
`upstream/main`. Entre esos cambios hay un arreglo para que el servidor
sobreviva a una limpieza masiva de terminales. Antes de usar el fork como base
productiva se debe rebasar sobre upstream y repetir la matriz de pruebas.

## Alcance de esta nota

Esta es una hoja de ruta de arquitectura; no implica que los cambios P0 estén
ya implementados. La integración `aipm_cao` conserva sus controles de
autorización y gobierno mientras las mejoras genéricas de CAO se desarrollan
en este fork.
