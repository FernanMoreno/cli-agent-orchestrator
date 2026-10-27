# T174 — turno Unicode interactivo de Codex

2026-10-27. **PASS acotado**, fuente ejecutada `7b2ea7d776e60614a583f3ece37865298d316302`, Codex CLI 0.164.0 y modelo solicitado `gpt-6-luna`. Un caso real, sin errores ni omisiones. [Recibo estructurado](validation-t174.json).

## Resultado observado

El `POST /terminals/run-step` real devolvió HTTP 200 en **12.267 s**, con la subcadena sintética completa: marcador único, español, japonés y un acento descompuesto `e` + U+0301, sin normalización. Se conserva el presupuesto de 180 s, `prompt_redelivery=False`, teardown y la instrucción normal de recibo; no se exige JSON al modelo ni igualdad de toda la respuesta.

El hijo quedó `succeeded`, con `settled_at` y `cleanup_completed_at`; join confirmó el mismo id, terminal, proveedor, estado y marcas temporales. Se usa la ruta interactiva de producción y sus extractores/verificación, no `codex exec --json`. El proceso padre fue observado por ejecutable fijado y argv: `--profile unicode-safe`, `--model gpt-6-luna`, sin `--yolo`. El hijo usa el mismo perfil construido por el runner; no se atestigua independientemente su modelo servido ni el CAS interno de generación del turno.

## Preparación y errores conservados

El primer intento nativo falló **antes de tener padre listo o enviar Unicode**, en a40dc720: la sonda de Codex reutilizó el ejemplo antiguo `[profiles.unicode-safe]` de CAOS. El CLI fijado lo rechazó. Se conservan 184,326 s de pytest/limpieza, el fallo original, fuente/binario/intérprete/login intactos, copia privada eliminada y descriptor cerrado. No fue un fallo Unicode ni una prueba de autorización del proveedor.

Se corrigieron el ejemplo en docs/codex-cli.md y las referencias del modelo/provider. CLI 0.164 usa `$CODEX_HOME/unicode-safe.config.toml` separado, con `approval_policy="never"` y `sandbox_mode="read-only"`. El diagnóstico mantiene apps/plugins/shell desactivados en su configuración privada. No se migra ni modifica configuración o login del operador. [Referencia oficial de perfiles](https://learn.chatgpt.com/docs/config-file/config-reference).

Antes de R2, el mismo binario ejecutó `--profile unicode-safe mcp list --json` sin credenciales y sin red mediante bwrap: negativo legacy EXIT1 y positivo separado EXIT0/[] pasan. Un intento previo con `features list` solo probó que ese subcomando no admite `--profile`; queda conservado y no se cuenta como carga válida. Las validaciones HTTP usan el OpenAPI de la API real.

La primera ampliación de contratos tuvo 106 errores de fixture por una invocación de Codex sin metadata PG; se conserva. La repetición utilizó el propietario PostgreSQL verificado, con autostart desactivado. Pasan 106 contratos compartidos, 8 controles Unicode actuales (corrupción/normalización, identidad y join/errores HTTP) y 6 contratos de perfil: **120 distintos**, en ejecuciones solapadas documentadas, no una supuesta suite única. Black/isort y composición 5/0 pasan. El RED inicial de la funcionalidad nueva fue un import ausente antes de implementar el helper.

## Fuente, recursos y composición

Se fijaron y comprobaron antes/después **1720 archivos**, el binario, intérprete y scripts privados. Credenciales originales intactas por identidad/bytes; copias diagnósticas retiradas. Los dos descriptores se archivaron ejecutados y se cerraron mediante cambio exclusivo de bandera.

Pytest y limpieza de R2: 48.594 s, medidos después del aprovisionamiento PG; se distinguen de los 12.267 s HTTP. Memoria disponible: 10131 MiB antes del launcher, 9773 antes del padre y 9697 antes del turno; mínimo exigido 6144 MiB.

API, HOME y raíces tmux propios cerrados; ausencia de las sesiones nativas propias y de 10 procesos observados por identidad de nacimiento. El observador solo lee stat/identidades y panes del socket privado, nunca entornos globales. No demuestra ausencia de cualquier proceso arbitrario que se hubiera desacoplado a otra sesión. Cero conexiones prestatarias y bases privadas antes de devolver al propietario PG; contenedor/red exactos ausentes y propietario EXIT0. No existía PG de CAOS al empezar; el servicio privado se reutilizó para los contratos y ambos intentos, sin tocar contenedores ajenos.

Revisión de composición **PASS para este recorrido**: perfil→argv/config nativos, esquema HTTP, extracción pública→recibo persistido→join y cierre de propietarios. No hay migración, endpoint nuevo, retry nuevo ni cambio de autoridad. Se usan PostgreSQL y CLI/API/tmux reales para las fronteras relevantes. Claude revisó el helper/launcher cerrado con GO y Luna revisó la corrección R2; el primer diagnóstico READ de Claude agotó tiempo y no cuenta como GO.

## Alcance pendiente

T174 cierra este baseline Unicode y ciclo del hijo. **CX-08 completo sigue abierto**: errores de autenticación/cuota, cancelación activa y reconciliación de timeout, además de observación independiente del CAS interno cuando corresponda. No acepta mensajes, cao.work.message, matriz ampliada T032, otros proveedores ni gates globales. CX-06/CX-07 y T173 mantienen sus fuentes/recibos originales.
