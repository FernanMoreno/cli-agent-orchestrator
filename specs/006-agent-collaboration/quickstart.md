# Guía de aceptación

Describe la ejecución prevista; runner pendiente de implementación, no acredita pruebas ejecutadas.

## Prerrequisitos

Docker, imagen candidata, tmux, Claude/Codex/OpenCode configurados y Escritorio accesible. El propietario solicitó expresamente la prueba con sus proveedores. No imprimir entorno/configuración MCP completos ni bearer.

## Secuencia

1. Ejecutar regresiones indicadas en [plan.md](plan.md) y `project-composition-check "$(cat .ai/project-name)"`.
2. Crear run_id y carpeta nueva del Escritorio; construir/reutilizar imagen candidata.
3. Usar el futuro `scripts/agent_collaboration_acceptance.py` con `--desktop`, `--image` y `--providers claude_code,codex,opencode_cli`. Son argumentos propuestos, aún no comandos disponibles.
4. Arrancar aplicación temporal con root/puertos/nombre propios y montaje exacto del proyecto; no reiniciar instalación personal.
5. Claude delega API a Codex y frontend a OpenCode; ambos acuerdan contrato por mensajes y devuelven resultados. Claude integra. Probar `handoff` con una tarea corta.
6. Verificar API/frontend con navegador, mensaje a receptor ocupado, directorio heredado, proyecto RO e integración externa.
7. Probar cancelación/cierre/reinicio, conservación de archivos y mensajes, y ausencia real de descendientes.
8. Conservar proyecto y resumen saneado; limpiar recursos temporales propios.

## Evidencia esperada

Tres proveedores/identidades reales; cero contenedores por trabajador; delegaciones y mensajes entre hermanos; operaciones API/frontend correctas; cambios host persistentes; rechazo RO; integración comprobada; cierre/recuperación. Escenarios ausentes quedan pendientes y no se sustituyen por mocks. Fallo exige reproducción y diagnóstico antes de corregir. Plazos acotados, sin reintentos indefinidos a proveedores.


## Aceptación mantenible verificada

```bash
.venv/bin/python scripts/agent_collaboration_acceptance.py --recovery \
  --project /mnt/c/Users/ferna/OneDrive/Escritorio/cao-collaboration-demo-20261001-cf8017b2
```

Requiere los tres CLI y logins vigentes en el mismo WSL/Linux. Usa una copia del
proyecto y recursos propios; incluye recuperación y fallos controlados. Cierre
actual, límites y pruebas: [informe detallado](cierre-20261005.md).
