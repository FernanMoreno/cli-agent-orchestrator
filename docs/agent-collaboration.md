# Colaboración local de agentes

CAO es una herramienta gratuita de trabajo local. Un coordinador puede dirigir
varios trabajadores Claude Code, Codex y OpenCode sobre el mismo proyecto. La
coordinación entre varias instancias CAO en esta PC se describe en
[coordinación local](../specs/009-local-cao-coordination/quickstart.md).

## Entorno y autoridad

Los agentes habituales se ejecutan en tmux, dentro del entorno de la aplicación
integrada o del mismo WSL/Linux. No necesitan un contenedor por trabajador.
Work conserva su ejecución opcional y sus contratos propios.

Los agentes comparten usuario y archivos accesibles. El ID de terminal permite
enrutar tareas y mensajes; no crea una cuenta ni una barrera de seguridad por
agente. Los worktrees separan cambios Git. Para restringir escrituras de un
proyecto completo se usan montajes Docker de solo lectura.

El MCP propio recibe dirección, directorio durable y autenticación de CAO.
Los overrides explícitos del perfil se conservan. CAO no agrega estas
credenciales a la configuración de MCP externos. Codex hereda nombres de
variables; un bearer explícito de su perfil se guarda en archivo privado
`0600`, se carga al entorno y se elimina al limpiar el proveedor. El CLI normal
envía el bearer mediante cabecera HTTP. Un token ausente, inválido o caducado
no autoriza operaciones protegidas. Renovar la sesión del proveedor y renovar
el bearer de CAO son operaciones diferentes.

## Delegación y mensajes

- `assign` crea trabajo asíncrono: el supervisor termina su fase de despacho
  y atiende cada callback como un turno posterior.
- `handoff` espera un resultado; el timeout conserva una referencia recuperable
  y no autoriza repetir automáticamente la tarea.
- `send_message` sin destinatario responde al caller. Entre trabajadores se
  usan IDs reales explícitos; un destinatario inválido no se sustituye.
- Aceptación del mensaje, entrega y resultado verificado son estados distintos.
  Un receptor ocupado conserva mensajes pendientes. Una entrega incierta se
  reconcilia; no se interpreta como permiso para reinyectar.
- La finalización necesita el recibo durable del turno actual. Una frase en
  pantalla o un recibo de una generación anterior no prueba éxito.

## Proyectos

El instalador admite `--workspace RUTA` y `--workspace-readonly RUTA`. Las rutas
se canonicalizan; modos distintos sobre proyectos solapados se rechazan.
El manifiesto conserva los montajes para recreaciones y rollback.

Cuando el despliegue configura `CAO_REGISTERED_PROJECTS`, debe contener una
lista JSON no vacía de rutas absolutas existentes. El directorio inicial del
terminal debe pertenecer a una de ellas. Se resuelven symlinks antes de admitir
la ruta y se rechazan escapes. Sin esta variable, CAO conserva su validación
genérica. Este registro controla la admisión del directorio, no todas las
acciones posteriores de las herramientas.

## Integraciones externas

Cada MCP externo mantiene su configuración y permisos. `127.0.0.1` señala el
entorno que ejecuta al proceso: dentro de Docker no equivale al loopback de
Windows/WSL. Registrar y comprobar una conexión concreta evita confundir esas
fronteras. El puente Windows existente conserva su allowlist; no se abre un
puente general del host.

## Cancelación y cierre

En Linux, cerrar un terminal limpia también hijos separados que conservan
su ID de terminal y socket tmux heredados. La selección comprueba usuario y
contexto; pidfd evita señalizar un PID reutilizado. La cancelación fija los
procesos antiguos antes de reemplazar la shell y conserva la nueva ventana.
Los fallos de confirmación se exponen; no equivalen a cierre exitoso.
Esto no es una garantía frente a un proceso que borra deliberadamente su
contexto heredado. No se matan procesos por nombre.

## Aceptación reproducible

Con los tres CLI instalados y sus sesiones vigentes en el mismo WSL/Linux:

```bash
.venv/bin/python scripts/agent_collaboration_acceptance.py \
  --project /ruta/al/cao-collaboration-demo
```

El runner reutiliza `validate_collaboration_demo.py`: copia el proyecto,
usa HOME/SQLite/tmux/puerto propios y realiza dos rondas. El modo completo
añade API autenticada, mensajes reales entre trabajadores, handoff y un MCP
externo contra un servicio HTTP privado, primero disponible y después detenido.
El controlador da órdenes; los agentes envían sus propios mensajes.

El preflight rechaza ejecutables o registros de login ausentes y una sesión
Claude caducada antes de crear recursos. Los escenarios tienen plazos; no hay
reintentos automáticos de asignaciones. La limpieza afecta exclusivamente al
runtime temporal. Los diagnósticos se conservan fuera del proyecto y no
incluyen copias de credenciales. Un fallo o prerrequisito ausente no acredita
aceptación global. El resultado vigente de spec006 se registra en
[el informe de cierre](../specs/006-agent-collaboration/cierre-20261005.md).


Para añadir renovación del bearer del controlador, reinicio con mensaje pending,
fallo de modelo nativo, cancelación durante deferred-init y handoff con timeout:

```bash
.venv/bin/python scripts/agent_collaboration_acceptance.py --recovery \
  --project /ruta/al/cao-collaboration-demo
```

La prueba de reinicio introduce únicamente una negativa temporal a verificar
un recibo real en ese servidor privado; nunca genera recibos ni reinyecta la tarea.
El frontend produce el mensaje pending y el mismo trabajador lo recibe tras
reconciliar. El bearer renovado pertenece al controlador; no se acredita recarga
automática del entorno de un MCP cuyo token ya haya vencido por ese runner genérico.

## Renovación en la instalación personal local

El emisor personal publica `mcp-bearer.jwt` con permisos 0600 bajo el directorio
privado de instalación. La variable `CAO_AUTH_LOCAL_TOKEN_FILE` permite que los
MCP ya abiertos lean la credencial actual en cada petición. La API sigue
validando firma, issuer, audiencia, caducidad y scopes. El token dura una hora
por defecto y se renueva cinco minutos antes de vencer; el proceso de API,
tmux y los proveedores permanecen abiertos.

Si el archivo está configurado y falta, es inseguro o no se puede leer, no se
usará el token antiguo del entorno. La petición será rechazada. Una petición
HTTP de mutación no se reintenta automáticamente al renovar. Al reparar la
fuente, la próxima llamada puede usar el token vigente. Un token estático
explícito en el perfil deshabilita la fuente heredada; un archivo explícito
del perfil mantiene la elección del operador.

La configuración privada `deployment.json` admite
`mcp_token_lifetime_seconds` entre 60 y 86400 segundos; omitirlo selecciona
3600. El valor 60 se utiliza únicamente en aceptación de caducidad real.
Los runtimes actualizados eliminan el reinicio periódico de 12 horas que
rotaba el token. La actualización inicial de la imagen requiere recrear el
contenedor; las siguientes renovaciones se hacen dentro del mismo runtime.

Esto renueva la credencial de CAO. Los logins OAuth de Claude, Codex y OpenCode
conservan el ciclo de vida de sus propios proveedores.

Véase [especificación y evidencia](../specs/014-local-mcp-token-renewal/spec.md).
