# T205 · Observación causal del apagado del proxy

Se añade un ledger privado opt-in al proxy diagnóstico: códigos fijos, reloj
monotónico, fd raw0600 exclusivo/append, una escritura por evento,64eventos y
16KiB máximos al leer. No registra frames, argv, entorno, prompts o credenciales.
El lector rechaza campos/códigos/tipos desconocidos, tiempo regresivo, symlinks
y archivo incompleto/permisos distintos.

Se observan EOF en ambas direcciones, señal recibida, clase de aborto, intención
de terminate/kill y waitstatus real del hijo; la señal conserva su comportamiento
previo (SIGINT KeyboardInterrupt, SIGTERM SystemExit). No es una corrección del
apagado: señales/limpieza no generan closed_cleanly ni el marcador normal. Una
segunda señal puede aún interrumpir finally: el padre mantiene la barrera de
retiro físico del grupo y una observación ausente queda desconocida.

TDD: RED por módulo ausente;16casos del ledger/proxy PASS. Selección final con
regresión de exclusión/HTTP/catálogo:83PASS/14,88s. Cuatro procesos reales cubren
EOF/SIGINT/SIGTERM/trama parcial, estado real del hijo y ausencia física final.
No se ejecuta Claude, modelos, API/PG ni auth en esas pruebas. Composición:
5contratos conservados/0rotos. Graphify no tiene estos nodos recientes; fuente
proxy/control/ownership y recibos T204 son la autoridad. No se regenera el grafo.

Lanzador privado derivado de T204r2: máscaras/no-egress/dummykey y catálogo
interaction3 intactos. Registra por separado stdin cerrado/exitCLI/corte6s del
worker y comienzo de limpieza del padre; se leen eventos sólo tras retirar el
grupo. Imports reales, builder/env/PYTHONPATH/ledger, binario y memoria validados
antes de armar. El preflight nativo sin turno se ejecutó después sobre fuente integrada; resultado abajo.

T205 se cierra sólo como diagnóstico; T204/CL07 siguen abiertos. Los recibos
T204 fallidos se conservan y no se reclasifican. Sin cambios de producto,
permisos, SDK, persistencia o límites.

Revisión Claude GO para integrar y un preflight zero-turn; exige ledger/witness
ausentes antes de lanzar y lectura sólo tras retiro del grupo. Ambas condiciones
se comprueban mecánicamente. No es aceptación de apagado o CL07.

## Diagnóstico nativo, 2026-10-28

Fuente32e5755d/528pins,40,542s/mínimo10621MiB. Catálogo3 conectado; al cerrar
stdin de Claude, el proxy recibe SIGINT2 antes de observar EOF propio, registra
KeyboardInterrupt y entra en limpieza. Se observa intención de SIGTERM al hijo
y waitstatus real -15 tras ese SIGTERM de la limpieza del proxy. El worker registra salidaCLI0 y vence el corte6s sin
closed_cleanly; la preflight sigue FAILED, sin marcador normal.

La señal se registró después de worker_stdin_closed y antes de la observación
de exitCLI y de parent_cleanup_started. Distingue esta señal de la limpieza
posterior del padre. No se observó la identidad del emisor: no se atribuye con
certeza a un PID concreto. La secuencia muestra por qué el proxy no alcanzó su
retorno normal; no demuestra la causa histórica504 ni un fallo de autorización.

[Recibo](validation-t205-native-r1.json):diagnosis_accepted=true,
native_preflight_accepted=false,native_client_denial_accepted=false. Namespace
y máscaras verificados,0turnos/no login copiado/noAPI/PG, fuentes intactas, grupo
retirado y descriptor recerrado tras verificar528pins/archivoejecutado inmutable.

Siguiente corrección a evaluar: cierre EOF explícito solicitado por el harness
al MCP cuando no haya RPC/datos pendientes, antes de cerrar la entrada de la
CLI, con espera de salida0 real y witness normal. Debe declararse como modo
controlado, preservar los6s y no convertir una señal en éxito ni acreditar
apagado automáticoT197. Aún es propuesta: no está implementado ese modo.

La marca parent_cleanup_started_ns procede del recibo privado conservado del
controller, escrita inmediatamente antes de retirar su grupo. El recibo público
proyecta sólo la comparación temporal. Revisión final Claude: GO; no se atribuye
un emisor concreto de SIGINT ni se acepta todavía la corrección propuesta.
