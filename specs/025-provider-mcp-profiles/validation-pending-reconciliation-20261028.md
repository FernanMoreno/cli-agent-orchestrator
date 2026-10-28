# Contraste de tareas históricas ·2026-10-28

Auditoría Luna Max de solo lectura, contrastada por Codex con código y recibos.
No ejecuta modelos, no cambia criterios y no etiqueta ejecuciones antiguas como
PASS en la fuente nueva. Tres cierres de alcance ya satisfecho:

| Tarea | Evidencia de cierre | Límites conservados |
| --- | --- | --- |
| T079 | Contradicción ACK/recibo reproducida/corregida y gate endurecido; T135 v2 genérico y full8 tienen ACK exacto y turno completado en ambas fases, reinicio y limpieza. | El intento T079 falló; no se explica toda su causa por un PASS posterior. |
| T092 | La regresión de la primera corrección T091 se reprodujo/corrigió en T093; T101 y después T134 aceptan v1 con nueva generación, mensajes/ACK/reinicio y limpieza. T135 acredita equivalencia completa525archivos+7artefactos entre la fuente ejecutada y su revisión aceptada. | T092/T094 siguen FAILED en sus recibos; no es una ejecución nueva sobre el HEAD de2026-10-28. |
| T117 | Corrección del perfil y clasificador observacional verificada; T135 genérico/full8 pasan bajo la instrucción canónica estricta de cuerpoACK más recibo independiente. | No atribuye una causa nativa/modelo al falloR6 ni transformaR7 en éxito. |

Fuentes primarias del repositorio: [T079](validation-t079.md),
[T092](validation-t092.md), [T093](validation-t093.md),
[T101](validation-t101.md), [T117](validation-t117.md),
[cierreT135](validation-t135-closeout.md),
[v1](validation-t134-v1-native.json),
[genérico v2](validation-t135-v2-generic-native.json),
[full8](validation-t135-v2-full8-native.json) y
[equivalencia v1](validation-t135-v1-source-equivalence.json).

Contraste con código actual: run_native_v2_acceptance._RECEIVER_PROMPT distingue
cuerpo de recibo; el gate exige receiver_ack_exact is True. opencode_cli conserva
la eliminación acotada de encabezados nativos bajo recibo activo y mantiene los
casos ambiguos cerrados. Los tres recibos citados tienen dos fases exitosas con
receiver_ack_exact y receiver_turn_completed_once True.

Se conservan abiertos T078/T084/T088/T089/T111/T112/T113/T127: no se obtuvo en
sus ejecuciones la observación causal o comparación nativa condicional requerida.
Un PASS posterior no reconstruye esa frontera histórica. T091 también se deja
abierto conservadoramente: T093 corrige la regresión de su primer filtro, pero el
registro T090 no retuvo la fila Thought literal que motivó el diagnóstico. No se
repite un recorrido sano para intentar forzar el fallo. Estas investigaciones se
retomarán sólo con evidencia nueva que distinga hipótesis. No se cierran gates
globales de perfiles, concurrencia, Work o aceptación de fuente final.
