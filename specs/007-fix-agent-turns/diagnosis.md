# Base del diagnóstico

Fecha: 2026-10-02. Apoyo para planificación; no acredita correcciones implementadas.

## Evidencia y límites

La [prueba del spec006](../006-agent-collaboration/composition-review.md) creó una demo real con tres proveedores. Servido estático y URL incorrecta pertenecían a la demo. IDs antiguos y búsqueda improductiva de herramientas fueron decisiones del modelo; no hay evidencia de enrutamiento defectuoso.

El grafo existente se contrastó con fuente actual, sin regeneración. Referencias siguientes relativas al repositorio.

## 1. Validación distinta del lanzamiento

- `scripts/docker_install.py:418–425` monta OpenCode en `/opt/cao/host-tools/opencode`.
- `docker/personal/Dockerfile:64` incluye esa carpeta en PATH del proceso.
- `scripts/docker_install.py:483–505` comprueba versiones mediante procesos directos, sin verificar la shell efectiva de lanzamiento.
- `src/cli_agent_orchestrator/providers/opencode_cli.py:258` detecta versión desde el proceso CAO; `:350` genera el comando `opencode` que se entrega a la terminal.
- Reproducción posterior de solo lectura en cao-personal: shell normal encuentra `/opt/cao/host-tools/opencode`; shell login termina con 127. La prueba anterior necesitó un enlace dentro de la app temporal.

Defecto acreditado: validación positiva no garantiza disponibilidad en la shell efectiva. El plan decidirá la solución sin imponer aquí un enlace o cambios globales de shell.

## 2. Perfil OpenCode ausente

- `src/cli_agent_orchestrator/services/install_service.py:723–744` materializa el perfil nativo.
- `src/cli_agent_orchestrator/providers/opencode_cli.py:263–270` construye configuración privada desde perfiles materializados.
- `src/cli_agent_orchestrator/utils/opencode_v2.py:103–118` recorre archivos existentes sin comprobar en ese punto la presencia del seleccionado.
- `src/cli_agent_orchestrator/providers/opencode_cli.py:355–356` pide el agente seleccionado al proveedor.

La prueba preparó el perfil CAO sin materializarlo en OpenCode: prerrequisito incumplido. La mejora consiste en detectarlo antes de lanzar y explicar cómo prepararlo; no instalar ni sobrescribir configuración del propietario silenciosamente.

## 3. Turno incierto y continuación bloqueada

- `src/cli_agent_orchestrator/providers/claude_code.py:321` activa recibos.
- `src/cli_agent_orchestrator/providers/base.py:358–373` añade la instrucción; `:387–395` bloquea mientras hay recibo pendiente.
- `src/cli_agent_orchestrator/services/terminal_service.py:3012–3021` rechaza nuevas tareas. El409 protege contra sobrescribir trabajo incierto; no es por sí mismo un defecto.
- `src/cli_agent_orchestrator/services/status_monitor.py:822–850` mantiene PROCESSING mientras verifica finalización visual.
- `src/cli_agent_orchestrator/services/terminal_service.py:388–430` limita reintentos pero publica reconciliación mediante transition_native_child.
- `src/cli_agent_orchestrator/clients/database.py:2918–2968` modifica NativeChildModel; devuelve None si no existe registro correspondiente.
- `src/cli_agent_orchestrator/services/inbox_service.py:116–131,180–190` conserva pendientes entregas bloqueadas antes de pegarse.

La continuación del supervisor ordinario no quedó resuelta: se necesitó otra sesión. Debe reproducirse determinísticamente el recorrido completo antes de corregir. Recibos y mensajes pendientes son protecciones que se deben conservar, no eliminar.

## 4. Falta de resultado clasificada como error interno

- `src/cli_agent_orchestrator/services/terminal_service.py:3776–3786` lanza OutputExtractionError si no obtiene resultado verificable con recibo pendiente.
- `src/cli_agent_orchestrator/api/main.py:4710–4716` convierte esa excepción en HTTP500.
- Respuesta conservada: `Receipt-bearing task has no verified result; reconcile the terminal`.

Defecto de clasificación: la condición esperada de pendiente/reconciliación comparte error interno con fallos reales de extracción. Debe distinguirse sin ocultar averías ni presentar transcripciones como resultados verificados.

## Comprobaciones realizadas

Tres pruebas existentes de test/services/test_turn_receipt_delivery.py pasaron durante el diagnóstico: bloqueo con recibo restaurado, reintentos acotados del verificador y protección de resultado no verificado. Acreditan protecciones actuales, no recuperación de supervisor ordinario.

Durante esta especificación no se cambian fuentes, configuración personal ni agentes. Plan, regresiones y tareas siguen pendientes.
