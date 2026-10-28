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
antes de armar. No se solicitó aún el nuevo preflight nativo sin turno.

T205/T204/CL07 permanecen abiertos hasta sus evidencias exactas; los recibos
T204 fallidos se conservan y no se reclasifican. Sin cambios de producto,
permisos, SDK, persistencia o límites.

Revisión Claude GO para integrar y un preflight zero-turn; exige ledger/witness
ausentes antes de lanzar y lectura sólo tras retiro del grupo. Ambas condiciones
se comprueban mecánicamente. No es aceptación de apagado o CL07.
