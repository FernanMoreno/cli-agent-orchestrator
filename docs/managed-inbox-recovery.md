# Recuperación del inbox managed

Esta guía describe el bridge interno de T018 y cómo tratar una operación cuyo
efecto no se pudo confirmar. T018 obtuvo un **PASS acotado**: estas garantías
no implican soporte para una ruta pública ni para todos los despliegues.

## Dos recorridos distintos

El inbox legacy y el inbox managed no comparten la interpretación de estado.
Los elementos managed se crean en `RECONCILE`, fuera del lector legacy que
procesa `PENDING`. Un elemento managed no debe pasar al recorrido legacy ni
tratarse como pendiente de envío para resolver una incertidumbre.

El bridge con inbox legacy se establece antes de habilitar la cola managed.
El contexto del servidor se fija y se vuelve a comprobar antes de reservar,
enlazar el bridge, habilitar la cola y ejecutar el efecto. El orden del bridge
no convierte `RECONCILE` en una confirmación de entrega.

## Efecto incierto

Si el proceso se interrumpe después de que pudo ocurrir el paste y no existe
confirmación durable, conserva el elemento en `RECONCILE`. No lo reenvíes y no
registres un ACK inferido. Tras un reinicio, T018 no repite el paste incierto.

T018 no define un procedimiento para decidir externamente si ese efecto ocurrió
ni una transición manual para cerrar el caso. Si la operación sigue sin
resolverse, mantenla en reconciliación y sigue el procedimiento de operación
autorizado para el entorno; no la conviertas en una nueva orden de envío.

## Identidad del store y migraciones

La migración v15 y su checksum permanecen literales; v16 conserva los bridges
v15 y añade un contexto singleton con UUID lógico no secreto y ruta canónica
inmutable. Se crean durante la transacción de migración. La inicialización
rechaza un contexto ausente o una identidad, esquema o ruta contradictorios.
La expansión contrasta el singleton y los bridges v15 con la ruta observada,
sin alterar las filas v15.

Las conexiones usadas para las operaciones managed comprueban la identidad de
su store contra el contexto fijado por el servidor y `DATABASE_FILE`; el
repositorio también coteja su propia ruta. Un UUID distinto permite detectar un
reemplazo dentro de ese contexto. El UUID representa identidad lógica: una
copia completa que conserva el UUID no demuestra que se trate del mismo archivo
físico, y un reemplazo completo entre procesos independientes no se detecta sin
un ancla externa.

## Fallo cerrado y rollback

Ante contexto ausente o divergente, store ausente o contradictorio, o bridge
managed no verificable, detén el avance managed y no habilites el efecto. La
autoridad asociada a contrato, grant, snapshot, reserva e intención durable se
revalida antes del efecto; no sustituyas esa validación por una inferencia del
operador.

El rollback detiene managed y conserva UUID, bridge, orden y fila
`RECONCILE`. Un lector anterior que no reconozca v16 debe detenerse. No debe
reinterpretar `RECONCILE` como `PENDING` ni degradar la fila para forzar el
procesamiento legacy. El rollback descrito es no destructivo respecto de esos
datos; no autoriza a editar o reconstruir el store operativo.

## Límites de T018

T018 cubre el bridge managed interno y sus comprobaciones de identidad. No
acredita el reemplazo completo del store entre procesos sin ancla externa, un
proveedor o backend real, la base de datos de un operador, una ruta pública ni
una API o CLI. Tampoco demuestra aislamiento físico ni compatibilidad con
todas las versiones desplegadas.

Fuentes de alcance aceptado: [estado T018](../specs/001-verifiable-orchestration/workflow-status.md#bridge-inbox-managed-t018-aceptado--2026-09-23) y [roadmap de orquestación](aipm-orchestration-roadmap.md).
