# T190 · preparación y diagnóstico de cierre nativo

Estado: **abierto**. No se han ejecutado las dos fases de mensajes originados
por el modelo; estos controles no acreditan CX-05, Work ni aceptación global.

La admisión existente permite seleccionar `CAO_SPEC023_PYTHON`, conservando
el valor histórico por defecto y la igualdad exacta con `sys.executable`.
Se amplía el enlace de fuentes a Codex, Claude y los propietarios compartidos
de permisos, mapeo y readiness. Se reprodujeron dos controles negativos antes
de corregir la selección; la suite enfocada terminó con **180 pruebas PASS**.
Composición: **5 contratos conservados, 0 rotos**. El cambio de admisión recibió
revisión independiente; no convierte la preparación en aceptación nativa.

## Observaciones reales sin turnos de modelo

- Un hijo MCP real, con el observador y servidor de las fuentes actuales,
  recibió EOF directo en stdin: salida 0, retorno normal y sello positivo
  genuino en **0,515 s**.
- Los controles de cierre de app-server y de terminal Codex no produjeron el
  sello. En el terminal, `/exit` devolvió HTTP 200 y se agotaron los 6 s de
  observación sin sello. No se enviaron mensajes ni se crearon filas/eventos.
- Un control adicional conservó el servidor/configuración canónicos e incorporó
  únicamente observación privada de señales. Al cerrar stdin del app-server,
  el hijo recibió **SIGTERM**; el padre nativo salió con código 0. La señal se
  registró **33,858 ms antes** de entrar en la limpieza forzada del controlador.
  No hubo retorno normal, evento atexit ni sello. Esto sitúa la terminación en
  el árbol controlado por el cliente nativo antes de nuestra limpieza; no
  identifica directamente el PID emisor ni demuestra toda modalidad TUI.
- Ese último control admitió **11119 MiB**, retiró todos los procesos propios,
  eliminó las copias privadas del login y verificó que el original no cambió.
- Un prototipo privado de lector interrumpible produjo EOF controlado y retorno
  normal/sello en **0,416 s**. No está integrado y **no acredita cierre automático
  nativo**. El prototipo anterior con cancelación AnyIO no desbloqueó readline.

Los recibos y controladores privados se conservan bajo
`/home/felni/.cache/caos-codex-native-messages-20261028-r1/` y las generaciones
`caos-codex-native-messages-20261028-admission-r1`/`admission-r2`.
Los fallos no se reclasifican como éxitos; los descriptores de admisión están
cerrados. El primer intento de instrumentación con un comando no canónico fue
rechazado antes del lanzamiento nativo; los siguientes conservaron ese contrato.

## Trabajo restante

Resolver el cierre del observador sin fabricar ni eliminar su sello positivo.
Mantener dos registros de dispatch exitoso, efectos exactos PostgreSQL/AG-UI,
identidad Unicode tras reiniciar, retirada física y preservación del login.
Distinguir cualquier cierre solicitado por el diagnóstico del cierre automático
del cliente nativo. Después ejecutar el recorrido existente con ambos actores
Codex reales. Los límites originales de **180 s** y **6144 MiB** permanecen.
Las revisiones de Claude disponen de **1800 s** por petición, por indicación del
usuario; ese margen no modifica los presupuestos de aceptación.

## Actualización: cierre diagnóstico implementado

El opt-in de Codex instala un lector que conserva el decodificador UTF-8 del
SDK y acepta una solicitud privada de EOF solo con dispatch quiescente. Se
rechazan llamadas activas, llamadas posteriores y bytes pendientes en el pipe;
ninguno de esos rechazos puede producir sello. El controlador exige dos
registros exitosos exactos antes de solicitar EOF, vincula PID/UID/nacimiento,
inodo de auditoría y pidfd, y confirma salida real/sello antes de borrar los
actores. Conserva los seis segundos de observación. El observador original,
el escritor del sello y su validador no cambian. Claude y producción mantienen
su transporte por defecto. El recibo distingue `harness_requested_eof` y deja
`native_automatic_shutdown_accepted=false`.

Regresiones:200 pruebas combinadas pasaron antes del último rechazo de bytes;
ese rechazo se reprodujo con un control RED y la suite del módulo terminó con
21 PASS. Arquitectura5/0 y revisión de Claude sin bloqueos tras corregir ese
hallazgo. No se omite el fallo del primer borrador.

El control nativo nuevo, sin turnos ni llamadas permitidas, pasó con
Codex0.164: catálogo exacto `send_message`, hijo retirado y sello genuino en
**0,356 s**, mientras el padre nativo seguía vivo.501 fuentes fijadas y
10747 MiB al admitir; fuente/login intactos, grupos propios cerrados y copia
de credenciales retirada. Su descriptor operativo volvió a cerrado y el
archivo ejecutado permanece intacto. Evidencia privada:
`caos-codex-native-messages-20261028-r1/native-eof-r1/receipt.json`.
Este control **no** acepta todavía las dos fases de mensajes con modelo.

Composición: el cambio se limita al diagnóstico; se verificaron la propagación
del opt-in, framing/Unicode del SDK instalado, autoridad de archivos/proceso,
orden de finalización antes de borrar terminales, errores sin sello y retiro
físico. No cambia permisos, API, esquemas, migraciones ni persistencia del
producto. Los cinco contratos de dependencia permanecen conservados.

Validación final del cambio revisado: **201 PASS en64,45s**, incluidos21controles
del módulo; black/isort pasan. No se ha iniciado aún el recorrido con modelo.

## Publicación atómica: validación previa a los turnos

Un control RED adicional demostró que crear el nombre final antes de escribir
permitía observar un marcador incompleto. Ahora el escritor reserva un nombre
privado pendiente, vuelve a comprobar ausencia del destino, escribe/fsync y
publica mediante rename atómico en el mismo directorio. Conserva el rechazo de
incumbentes y limpia solo su propia reserva. No se toleran marcadores inválidos
para evitar el fallo. **203 pruebas PASS en53,11s**, arquitectura5/0 y revisión
final de Claude sin bloqueos.

La generación completa r2 se cerró antes de ejecutarse: cero servicios,
credenciales copiadas o turnos. Un control nativo nuevo de la corrección pasó
con501pins y10958MiB: salida del hijo/sello en **0,358s**, antes de cerrar al
padre, login/fuente intactos y grupos/copias propios retirados. Evidencia privada:
`caos-codex-native-messages-20261028-r1/native-eof-r2/receipt.json`.
La generación r3 del recorrido con modelo está preparada y sigue cerrada hasta
fijar el commit de esta corrección. Los recibos anteriores se conservan bajo
sus propias fuentes; no se les atribuye retrospectivamente el código nuevo.

## R3 real: fallo conservado, limpieza completa

La generación r3 ejecutó la fuente42a55705 con502pins, Codex0.164 y actores
registrados Codex. Falló en faseA: el arnés registró `no_tool_call`, estado
terminal `completed`,29,328s de espera y0dispatch/filas/eventos. No hubo reinicio
de API. Duración total83,864s y memoria mínima9776MiB.20identidades observadas,
API/tmux/hijos y observador retirados; PostgreSQL propio y red exactos ausentes,
contenedores previos preservados, copias de login retiradas y original intacto.
El descriptor quedó cerrado y el recibo/archivo ejecutado se preservan.
[Recibo redaccionado](validation-t190-native-r3.json).

Ese `completed` no estaba correlacionado al turno aceptado por POSTinput: el
arnés descartaba `turn_sequence`. No acredita que el modelo terminara ese turno.
La respuesta real del modelo no se conservó antes de la limpieza. Se investigan
por separado la correlación causal y la oferta/diferimiento de herramientas;
ninguna hipótesis queda demostrada por este recibo. T191 reproduce el fallo de
validación del ACK y la clasificación de estado obsoleto. T190 sigue abierto;
no se repite el mismo diagnóstico sin una observación nueva.
