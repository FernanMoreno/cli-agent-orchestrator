# Investigación de colaboración

**Fecha**: 2026-10-01. Fuente estática; el comportamiento requiere comprobaciones ejecutables.

## Coordinación

**Decision**: Reutilizar `_assign_impl`, `_handoff_impl`, `_send_message_impl` en `utils/orchestration.py`, caller y `InboxService`. **Rationale**: ya resuelven delegación y mensajes; Graphify enlaza estas herramientas con sus consumidores, contrastados contra código porque sus líneas están desfasadas. **Alternatives considered**: nuevo bus o conexión directa entre contenedores; duplicación innecesaria. Upstream local inspeccionado: `2fcc3efa`, sin afirmar actualidad remota.

## Entorno MCP y autenticación

**Decision**: Reproducir y corregir solo reenvío necesario al MCP de CAO. **Rationale**: `personal_deployment.py:environment` configura API/auth; tmux transmite variables CAO y terminal ID. `providers/codex.py:1306` solo declara terminal ID en `env_vars`; developer no declara env. `security/auth.py:get_local_bearer` necesita configuración auth. Claude solo inyecta explícitamente terminal ID en MCP JSON privado; comprobar herencia. OpenCode traduce `env` a `environment` en `utils/opencode_config.py` y crea config v2 privada en `utils/opencode_v2.py`. **Alternatives considered**: token en argv o API sin auth; incompatibles con protección y privacidad.

`cli/commands/launch.py:510` ordinario no pasa bearer mientras ruta Work sí; reproducir. Puede usarse API autenticada para aceptación. Primera sesión y ventanas nuevas tienen rutas de entorno distintas (`clients/tmux.py:752`, `:923`); probar ambas.

Bearer compartido no significa identidad criptográfica por agente. Verificar expiración/renovación separadamente de sesión web. No reenviar secretos a MCP ajenos.

## Tres proveedores y Escritorio

**Decision**: Claude supervisor, Codex API, OpenCode frontend, perfiles con provider explícito. **Rationale**: petición expresa y resolución de hijos puede heredar proveedor del padre. **Alternatives considered**: un único proveedor o runner escribiendo demo; no acredita colaboración pedida.

Escritorio `/mnt/c/users/ferna/onedrive/escritorio`; la app actual monta su subcarpeta `caos`, no todo el Escritorio. Crear carpeta nueva exacta y despliegue temporal con misma imagen candidata/estado privado/puertos propios. No reiniciar aplicación personal ni modificar configuraciones globales al crear perfiles de prueba. Comprobar disponibilidad y modelo OpenCode antes de llamadas reales.

## Proyectos y lifecycle

**Decision**: Reutilizar binds/worktrees y añadir RO explícito/admisión de rutas; verificar lifecycle real antes de corregir. **Rationale**: `docker_install.py:runtime_mounts` solo ofrece proyectos RW por CLI, aunque `mount_argument` admite RO. `utils/path_validation.py` no valida pertenencia a registro. `worktree_service.py` ya usa `.cao/worktrees`. El teardown comprueba tmux desaparecido, no descendientes; aceptación Docker anterior no contiene agentes activos. **Alternatives considered**: montar todo HOME/Escritorio o dar por concluido al ver API healthy; no acredita límites ni limpieza.

No reenviar mensajes `reconcile`: enum y locks actuales separan incertidumbre de pendiente. Comprobar metadatos Git fuera del bind para worktrees vinculados antes de permitirlos.

## Integraciones

**Decision**: Conservar relay Windows MCP y comprobar desde cada agente la conexión seleccionada. **Rationale**: `docker_windows_mcp.py` ya conecta herramientas Windows autorizadas; localhost cambia de significado entre host/contenedor. **Alternatives considered**: bridge general o host networking global; innecesarios para mensajes locales.

## Resultado

Decisiones de producto resueltas; verificaciones experimentales quedan en tareas: propagación MCP de los tres proveedores, disponibilidad/modelo OpenCode, descendientes y reinicio. Resultado negativo exige diagnóstico/corrección mínima, no sustitución de arquitectura. Investigación hecha por agentes de solo lectura, sin llamadas de pago ni cambios de servicio.
