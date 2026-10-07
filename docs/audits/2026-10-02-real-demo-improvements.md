# Segunda aceptación real: mejoras del mismo demo de notas

## Resultado

**PASS** sobre el mismo proyecto existente `cao-turn-recovery-demo-20261002-f9086a25`. Las mejoras permanecen en ese demo; CAO se ejecutó desde la fuente actual de `main`, coincidente con los 384 hashes de la integración 008. No se hizo commit, push, despliegue ni publicación.

El operador pidió tareas nuevas una sola vez mediante `POST /terminals/run-step`, con `prompt_redelivery=false`, directorio del demo y perfiles nativos reales. Un padre Claude mantuvo la sesión y la identidad estructural. La ordenación de los tres encargos la realizó el controlador de aceptación; esta prueba no demuestra que un supervisor haya decidido o delegado tareas autónomamente ni que hubiera mensajes entre pares. Ningún proveedor fue simulado.

| Proveedor | Modelo | Encargo | Resultado durable |
|---|---|---|---|
| Codex | `gpt-6.1-sol` | DELETE, validación de texto, lock, escritura atómica, pruebas y contrato | HTTP 200, `completed`, recibo `succeeded` |
| OpenCode | `opencode-go/kimi-k3` | Búsqueda, filtros, contadores, borrado, controles accesibles y errores visibles | HTTP 200, `completed`, recibo `succeeded` |
| Claude Code | `sonnet` | Revisión de integración, dos pruebas adicionales, maxlength y documentación | HTTP 200, `completed`, recibo `succeeded` |

Codex estuvo temporalmente en reconciliación durante el trabajo y terminó verificado mediante observación automática del recibo auténtico. No hubo llamadas manuales a verify/cancel, reenvíos de instrucciones, cambio de modelo ni repetición de tareas. Los otros dos recibos también quedaron verificados. Las observaciones conservadas comienzan después del estado transitorio de Codex; no se presenta ese tramo como una traza completa.

## Validación independiente de la versión final

- `python -m unittest -v test_api`: **5 pruebas pasan**, con HTTP real, almacenamiento temporal, reinicio del servidor, concurrencia, validación, borrado exacto y servido estático.
- Probe HTTP externo: **20 comprobaciones pasan**, incluidas 24 creaciones concurrentes sin pérdida de actualizaciones, JSON inválido, persistencia y DELETE repetido con 404.
- Chromium real: **23 comprobaciones pasan**. Añadir/marcar/recargar, búsqueda sin distinguir mayúsculas, filtros, contadores, borrado, labels accesibles, error de red visible conservando el texto, recuperación posterior, HTML mostrado como texto y tres eventos submit durante una petición pendiente producen una sola creación. Cero errores JavaScript de ejecución.
- `node --check frontend/app.js`: pasa.
- `notes.json` original: mismo SHA256 antes y después; las pruebas funcionales usan datos temporales separados y las fuentes/frontend auténticos del mismo demo.

Estas comprobaciones se solapan y no constituyen una nueva ejecución de toda la regresión de CAO. Verifican los modelos y tareas indicados; no certifican todos los modelos, errores externos o comportamientos posibles.

## Recursos y evidencia

La sesión nativa, servidor CAO y servidor HTTP/Chromium de aceptación fueron cerrados. Se eliminaron las cuatro copias de login del HOME privado; las credenciales personales no se modificaron. La copia inicial completa del demo permanece en el directorio privado para comparación. Sólo se detuvo el proceso propio del servidor de navegador, validando comando y propietario.

Evidencia pública e índice de hashes (`evidence/2026-10-02-real-demo-improvements/evidence-index.json`; artefacto histórico local), recibos durables (`evidence/2026-10-02-real-demo-improvements/public-results.json`; artefacto histórico local), prueba HTTP (`evidence/2026-10-02-real-demo-improvements/http-proof.json`; artefacto histórico local), prueba Chromium (`evidence/2026-10-02-real-demo-improvements/browser-proof.json`; artefacto histórico local), captura (`evidence/2026-10-02-real-demo-improvements/demo-improved.png`; artefacto histórico local). Se conservan los controladores utilizados y una copia de las nueve fuentes/documentos finales del demo, sin copiar sus notas ni credenciales.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
