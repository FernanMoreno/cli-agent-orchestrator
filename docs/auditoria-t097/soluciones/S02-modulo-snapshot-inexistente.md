# S02 — Implementar `work_bubblewrap_runtime_snapshot` e integrarlo

Causa: [C02](../causas/C02-modulo-snapshot-inexistente.md)

## Módulo `src/cli_agent_orchestrator/services/work_bubblewrap_runtime_snapshot.py`

Contrato mínimo (ya fijado por `test/security/test_work_bubblewrap_runtime_snapshot.py`):

- `prepare_runtime_snapshot(runtime, source, deps, *, parent, max_bytes=..., max_files=...)`
  → objeto context-manager con `path`, `runtime_path`, `package_path`,
  `deps_path`, `validate()`, `close(cleanup_confirmed: bool)`.
- `RuntimeSnapshotError` para todo rechazo.
- Copia recorriendo con `os.scandir(follow_symlinks=False)` + `O_NOFOLLOW`:
  - ficheros regulares: copia de contenido + registro de digest SHA-256;
  - directorios: recrear con modo restringido;
  - symlinks relativos que resuelven dentro del mismo árbol: preservar;
    symlinks que escapan: `RuntimeSnapshotError("... symlink ...")`;
  - socket, FIFO, dispositivo: `RuntimeSnapshotError("unsupported ...")`
    sin modificar el origen.
- Límites `max_bytes`/`max_files` con mensajes "byte limit"/"file limit".
- Tras copiar: ficheros `0o444`; los que eran ejecutables conservan sólo `0o555`
  para que el intérprete pueda arrancar. Directorios quedan `0o555`; `validate()` recalcula digests
  y modos y lanza "changed" ante manipulación.
- `close(cleanup_confirmed=False)` conserva el snapshot (residuo incierto,
  coherente con el cleanup `UNCERTAIN`); `True` lo elimina.
- `WorkBubblewrapRuntimeIsolationProof`: ver S04; se emite sólo tras un ACK
  válido sobre un lanzamiento que usó este snapshot.

## Integración

1. `_launch_owned_stage` prepara el snapshot antes de construir argv y lo
   valida (`validate()`) justo antes de `Popen`.
2. `_build_bwrap_argv` recibe el snapshot y monta `snapshot.runtime_path`,
   `snapshot.package_path`, `snapshot.deps_path` (y los ficheros mínimos de
   `/usr`/`/lib`, ver S01) en vez de rutas del host. La composición copia sólo
   `bin`/`lib` del runtime y el cierre Pydantic usado por el bootstrap; excluye
   `share`, headers y paquetes no importados.
3. El snapshot vive bajo un directorio `0o700` privado del broker, fuera de
   cualquier ruta visible por otros intentos.
4. Tras cleanup confirmado, `close(True)`; con cleanup `UNCERTAIN`,
   `close(False)` y registrar el residuo.

## Verificación

- Pasar `test/security/test_work_bubblewrap_runtime_snapshot.py`.
- Test compuesto de S01 (socket tardío) en verde con Bubblewrap 0.13.

## Estado parcial implementado

- Módulo snapshot disponible con límites, manifiesto SHA-256, modos de solo
  lectura y cleanup condicionado al reap.
- La composición prepara y valida el snapshot justo antes de `Popen`; el argv
  monta esas copias privadas, no los árboles mutables originales.
- El intérprete conserva `0555` sólo si el origen tenía bit de ejecución; los
  demás ficheros quedan `0444`.
- Pruebas de socket tardío, mutación del origen después del ACK, setup fallido,
  cleanup incierto y retención del snapshot pasan en el harness local. La
  prueba directa de dispositivos hace `skip` si el runner no permite `mknod`.
- El host local no usa un kernel nativo ABI 9 y `/usr`, `/lib`, `/lib64` siguen
  como binds vivos; su defensa observada es seccomp `connect=EPERM`. La suite
  ABI 9 pasó en el guest fijado (S08); no hay target de producción planeado.
