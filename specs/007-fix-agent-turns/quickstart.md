# Validation guide

Desde raíz del repositorio; usar .venv/bin/python y web/node_modules existentes. No iniciar ni reiniciar instalación personal.

1. Ejecutar regresiones OpenCode en test/providers/test_opencode_cli_unit.py y test/providers/test_opencode_v2.py. Perfil temporal; binario temporal; shell login reemplaza PATH. Perfil ausente debe fallar sin send_keys.
2. Ejecutar test/services/test_turn_receipt_delivery.py y pruebas spec007 de recuperación. Supervisor sin NativeChild debe alcanzar reconcile tras tres verificaciones, sin paste nuevo.
3. Ejecutar pruebas de persistencia con SQLite temporal: reinicio, carreras cancel/verify, generaciones antiguas, resultado estable y rollback. Inbox mantiene pendientes hasta settlement probado.
4. Ejecutar contratos API y CLI/MCP; pending=202, reconcile=409, resultado=200, avería=500; FULL=transcripción.
5. Ejecutar pruebas web y TypeScript. Comprobar acciones usan misma generación, mensajes comprensibles y transcripción accesible.
6. Ejecutar project-composition-check "$(cat .ai/project-name)" y revisar diff final.
7. Prueba real aislada: seguir patrón documentado en specs/006-agent-collaboration/composition-review.md. Preparar perfiles mediante cao install antes de arrancar. Claude supervisor delega Codex/OpenCode; ida/vuelta por inbox; integrar pequeña app y comprobar navegador. Guardar evidencia saneada y lista de intervenciones. Cerrar solo recursos propios, conservar proyecto del Escritorio. Si falta prerrequisito, registrar caso no ejecutado y no afirmar aceptación SC-006.
