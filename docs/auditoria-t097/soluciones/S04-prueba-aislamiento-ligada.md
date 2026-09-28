# S04 — Ligar la prueba de aislamiento al intento, contrato y proceso

Causa: [C04](../causas/C04-prueba-aislamiento-no-ligada.md)

## Implementado

`WorkBubblewrapRuntimeIsolationProof` se emite por la ruta privada de
composición después de validar el ACK del bootstrap y guardar la evidencia
mediante `record_pre_go`. El objeto es inmutable y usa un sello privado del
módulo. Liga:

- intento, generación, revisión y hash del contrato;
- digest del snapshot y SHA-256 de la identidad Bubblewrap persistida;
- PID y starttime del monitor e init, sus pidfd, identidad del PID namespace y
  el inode del user namespace del init;
- identidad `(st_dev, st_ino)` del socketpair del worker, cuando hay proxy.

`require_current(attempt_id, generation, contract_hash)` compara la identidad
solicitada; valida otra vez el snapshot; comprueba que ambos pidfd siguen
abiertos para los PID esperados y vivos; revalida starttimes e inodes de PID y
user namespace; lee la identidad durable del repositorio; y confirma que el
init sigue en un user namespace distinto del broker. También exige Yama
`ptrace_scope=1`. La activación del proxy compara además revisión y socket exacto.

Orden del proxy: consumir la emisión durable y reservar socketpair antes del
lanzamiento; enviar el extremo del worker por `SCM_RIGHTS`; exigir en el ACK
FD 3, socket y `(st_dev, st_ino)` esperados; persistir setup antes de `GO`;
emitir proof y cargar el secreto sólo entonces. El guard se vuelve a ejecutar
antes de cada efecto. La revocación del mismo intento cierra el endpoint y
espera a que la solicitud en curso termine su llamada upstream, el registro
durable y el envío acotado de respuesta; no cancela un efecto ya iniciado.

## Límites y evidencia

- Hay pruebas negativas para intento, generación, hash y proceso terminado,
  además de snapshot alterado, starttime cambiado y PID namespace cambiado.
- El proof comprueba ambos starttime contra `/proc`, el inode de PID namespace,
  los pidfds y la identidad durable en cada guard.
- El rechazo de hermanos del mismo UID depende también de seccomp + Yama
  `ptrace_scope=1`, verificado en [S03](S03-proxy-socketpair-por-fd.md).
- La integración completa del flujo y la aceptación del perfil guest pasaron
  en S07/S08. No se prevé un host de producción, por lo que el backend sigue
  sin registrar.
- Bubblewrap no tiene `--preserve-fds` en la versión fijada por el harness;
  el traspaso usa un canal Unix de control y `SCM_RIGHTS`.
