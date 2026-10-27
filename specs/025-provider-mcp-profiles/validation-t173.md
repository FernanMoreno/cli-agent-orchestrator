# T173 — filtro MCP de comandos de texto y catálogo nativo Codex

2026-10-27. **PASS acotado** en `96f3eaae41de81601318688e1366c3ae201db4ee`, publicado en main antes de ejecutar. Codex CLI0.164.0; 21 casos reales del app-server, sin iniciar un turno de modelo. [Recibo estructurado](validation-t173.json).

## Corrección y pruebas

El overlay compartido solo traducía `enabled_tools` al filtro del servidor para comandos en array. Codex usa comandos de texto: el cliente restringía su catálogo, pero un acceso directo al servidor conservaba el perfil completo. Ahora ambos dialectos reciben el filtro interno; el texto conserva además el campo nativo. `None` conserva el perfil y `[]` deniega todo. Los nombres ajenos no amplían el perfil ni conceden autoridad por operación.

RED corregido:21fallos/7aciertos antes del cambio; se conserva aparte un primer defecto de anotación del arnés. GREEN final:194 pruebas/100,91s;28 casos nuevos recorren la composición de los7perfiles. Dos controles adicionales ejecutan el servidor MCP real por stdio sin cliente Codex: verifican catálogo0/1, rechazo directo, capacidad ausente y recursos/prompts ocultos. Sin PostgreSQL ni llamadas al modelo. Black/isort pasan; composición5contratos conservados/0rotos.

## Matriz nativa

| Perfil | Completo | Vacío | Parcial |
| --- | ---: | ---: | ---: |
| interaction | 3 | 0 | 1 |
| collaboration | 8 | 0 | 1 |
| coordination | 12 | 0 | 1 |
| supervision | 11 | 0 | 1 |
| knowledge | 10 | 0 | 1 |
| knowledge_writer | 12 | 0 | 1 |
| elastic_worker | 9 | 0 | 1 |

Cada caso compara los nombres exactos, confirma descubrimiento real con readiness y rechaza una llamada válida excluida. En completo/parcial, `cao_ask_user` llega al handler pero la ausencia de capacidad rechaza el efecto con `capability_unavailable`. Los rechazos nativos observados son del cliente, no prueba adicional del middleware del servidor. Solo se acepta el código-32603 unido al mensaje exacto de herramienta deshabilitada; un error genérico no cuenta como denegación.

El par adicional mantiene dos clientes con procesos propios distintos y catálogos3/12: iniciar el segundo no cambia el primero. Esto acredita estabilidad del catálogo, sin demostrar separación de todas las variables del entorno ni operaciones concurrentes autorizadas.

## Vinculación y limpieza

Se fijaron 1718 archivos fuente seguidos por Git, el runner y el binario; permanecieron iguales. El intérprete del checkout canónico coincidió con su hash al arrancar; el controlador no volvió a medirlo al terminar. El controlador privado se revisó cerrado, se armó cambiando únicamente su bandera, archivó los bytes ejecutados y se volvió a cerrar. Credenciales originales conservadas por identidad/bytes, copia privada retirada y todos los grupos de procesos propios cerrados. Mínimo disponible de los21casos: 10087 MiB; cada arranque exige6144MiB.

Los intentos admission-r1/r2/r3 fallidos quedan conservados como defectos del lector de rechazos; admission-r4 pasó los3casos de interacción. No se relabelan como aceptación. Revisión final ClaudeGO y LunaGO; se corrigió la frase sobre el intérprete para distinguir su verificación inicial de las comprobaciones finales de fuente/binario. Las pruebas fueron ejecutadas por Codex.

## Revisión de composición y límites

PASS para overlay→TOML nativo→entorno hijo→filtro de catálogo/dispatch y cierre de procesos. Se revisaron los perfiles inmutables, autoridad interna preservada, rechazo previo al dispatch, ausencia de estado/DB nuevo y separación entre configuraciones concurrentes. Los contratos no requieren backend porque los efectos se deniegan por falta de capacidad.

Esta ejecución desactiva apps/plugins/shell solo como contención diagnóstica. No cambia la política global de producción ni completa el mapeo de `allowed_tools`. No acepta efectos autorizados de cada herramienta, turnos del modelo, mensajes persistidos, `cao.work.message`, Unicode/cancelación ni gates globales. T173 queda cerrado; CX-01–04/CX-AUTH-03 conservan su alcance pendiente y reciben esta evidencia parcial. Los resultados OpenCode/Claude no se infieren de este Codex.
