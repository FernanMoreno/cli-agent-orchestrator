# Coordinación entre instancias CAO locales

La coordinación local permite que dos o más procesos CAO independientes se
repartan tareas de un mismo proyecto en **una sola PC**. Cada instancia conserva
sus propios perfiles, terminales, base de datos y ciclo de vida. No requiere una
cuenta, servidor alojado, relay ni conexión CAO entre equipos.

Esta guía cubre instancias que corren en el mismo entorno del sistema, por
ejemplo dos procesos CAO dentro de WSL. No cubre descubrimiento entre Windows,
WSL y Docker ni coordinación entre computadoras.

> **Confianza local:** los permisos de par restringen las operaciones de la API
> de coordinación y el proyecto seleccionado. No son un sandbox del sistema
> operativo. Los agentes conservan los permisos de archivos y procesos de la
> cuenta local que ejecuta CAO; empareja solo perfiles que consideres confiables.

## Cómo se separan las instancias

Cada `cao-server` necesita su propio `CAO_HOME_DIR` y un puerto distinto. El
directorio de perfil contiene su configuración, base de datos, identidad y
clave privada de firma. El registro que permite descubrir procesos activos es
compartido por el usuario local en
`~/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3`.

Abre dos terminales y deja cada servidor ejecutándose en primer plano. Usa el
host IPv4 loopback admitido por defecto y valores distintos de puerto y nombre:

**Terminal A — perfil `coordinador`:**

```bash
CAO_HOME_DIR="$HOME/.cao/perfiles/coordinador" \
CAO_API_HOST=127.0.0.1 \
CAO_API_PORT=9889 \
CAO_INSTANCE_NAME=coordinador \
cao-server
```

**Terminal B — perfil `implementador`:**

```bash
CAO_HOME_DIR="$HOME/.cao/perfiles/implementador" \
CAO_API_HOST=127.0.0.1 \
CAO_API_PORT=9890 \
CAO_INSTANCE_NAME=implementador \
cao-server
```

Mantén las mismas variables de cada perfil al ejecutar sus comandos `cao` y al
lanzar sus agentes. Configura en el perfil destino los perfiles de agente que
quieras recibir. `CAO_INSTANCE_NAME` solo cambia el nombre visible; la identidad
persistente la genera CAO.

La primera versión de descubrimiento requiere `127.0.0.1`; `localhost` y
`0.0.0.0` se anuncian como esa misma dirección. Los demás hosts, incluidas
direcciones IPv6, no se publican como pares aunque se añadan a
`CAO_ALLOWED_HOSTS`.

## Emparejar un proyecto

Los dos perfiles deben resolver el proyecto a la misma ruta canónica local. En
un repositorio Git, CAO identifica la raíz del repositorio y el directorio Git
común; en un proyecto sin Git, usa la ruta canónica del directorio.

1. Desde el perfil de origen, revisa las instancias activas para el proyecto:

   ```bash
   CAO_HOME_DIR="$HOME/.cao/perfiles/coordinador" CAO_API_PORT=9889 \
     cao peer list --project /ruta/al/proyecto
   ```

2. Empareja la identidad elegida para ese proyecto:

   ```bash
   CAO_HOME_DIR="$HOME/.cao/perfiles/coordinador" CAO_API_PORT=9889 \
     cao peer pair <instance-id> --project /ruta/al/proyecto
   ```

   CAO muestra la identidad, el proyecto, las acciones solicitadas y un código
   temporal de un solo uso que vence en cinco minutos.

3. En la terminal del perfil destino, revisa y acepta el reto. Introduce el
   código temporal cuando CAO lo pida:

   ```bash
   CAO_HOME_DIR="$HOME/.cao/perfiles/implementador" CAO_API_PORT=9890 \
     cao peer accept <challenge-id>
   ```

4. Comprueba el permiso desde ambos perfiles:

   ```bash
   cao peer list --project /ruta/al/proyecto
   ```

El emparejamiento guarda la clave pública del otro perfil y los scopes acordados.
Cada petición se firma con la clave privada de su propio perfil, que no se
transmite. El token maestro de CAO tampoco se envía al par.

## Asignar y revisar tareas

Los agentes pueden usar estas herramientas MCP cuando su perfil tiene acceso a
las herramientas de asignación de CAO:

- `list_local_cao_peers`: lista pares autorizados para el proyecto del terminal
  solicitante.
- `assign_local_cao_task`: envía una tarea con una clave de operación estable.
- `get_local_cao_task`: consulta el estado o recupera el recibo de esa tarea.
- `cancel_local_cao_task`: solicita parar la tarea y devuelve si CAO confirmó
  la parada.

Reutiliza la misma clave de operación al reintentar una petición incierta. La
misma clave con contenido distinto produce un conflicto. `cao peer status
<task-id>` también permite inspeccionar un recibo desde el perfil de origen o
destino.

El destino exige que el perfil de agente solicitado exista localmente. No se
reenvían mensajes de vuelta a una dirección elegida por el agente ni se permite
salir del proyecto vinculado.

### Proyectos Git

Cada tarea coordinada trabaja en un worktree separado de CAO. El recibo incluye
la ruta del worktree para que puedas revisar su diff antes de integrar cambios
en la rama o directorio principal. Varios pares pueden avanzar en worktrees
separados sin escribir simultáneamente en el checkout compartido.

### Proyectos sin Git

Los trabajadores modifican el directorio compartido. CAO usa un lease en el
registro local para permitir una sola tarea de par escritora a la vez. Si el
proceso destino se interrumpe, el lease queda retenido hasta confirmar que el
trabajador dejó de escribir y revisar el proyecto:

```bash
CAO_HOME_DIR="$HOME/.cao/perfiles/implementador" CAO_API_PORT=9890 \
  cao peer status <task-id>
CAO_HOME_DIR="$HOME/.cao/perfiles/implementador" CAO_API_PORT=9890 \
  cao peer reconcile <task-id>
```

La reconciliación pide confirmación y libera el lease. La tarea sigue marcada
como pendiente de revisión; no se convierte en éxito. Este lease coordina las
tareas de par sin Git. No bloquea ediciones manuales ni otras tareas locales
que se inicien fuera de esta función.

La coordinación de pares usa asignaciones ordinarias. No sustituye ni aprueba
Managed Work; si el modo de ejecución configurado exige Managed Work, el envío
de tareas de par se rechaza.

## Revocar y recuperar

Revoca un permiso para un proyecto desde el perfil que lo concedió:

```bash
cao peer revoke <instance-id> --project /ruta/al/proyecto
```

La revocación local bloquea inmediatamente nuevas operaciones salientes y CAO
intenta revocar también el permiso recíproco. Si el otro proceso está apagado,
el comando informa que solo pudo revocar el permiso local; cuando vuelva a
estar disponible, revócalo también desde ese perfil.

Una tarea activa conserva su recibo y puede quedar `interrupted` o `reconcile`.
Consulta el estado antes de repetir trabajo. El inicio de CAO reconcilia recibos
pendientes y libera un lease de perfil cuando no existe el recibo de tarea que
debía preceder a la creación del trabajador.

## Diagnóstico rápido

| Síntoma | Qué revisar |
|---|---|
| No aparecen instancias | Confirma que ambos servidores corren, comparten el mismo usuario y entorno, tienen puertos distintos y usan un host loopback permitido. |
| El perfil ya está ejecutándose | Cada servidor debe usar un `CAO_HOME_DIR` diferente. |
| No acepta el emparejamiento | Confirma el código, el reto vigente, la identidad del proceso y que ambos perfiles resuelven el mismo proyecto. |
| El destino no tiene el agente solicitado | Instala o configura ese perfil de agente en el `CAO_HOME_DIR` destino. |
| El proyecto sin Git sigue ocupado | Detén el trabajador, revisa los cambios y ejecuta `cao peer reconcile` en el perfil destino. |
| La petición quedó incierta | Consulta `cao peer status`; reintenta con la misma clave de operación. |

## Fuera de alcance

La coordinación descrita aquí conecta CAO independientes de una sola PC dentro
del mismo entorno de ejecución. No añade un SaaS, un directorio central, un
relay ni la coordinación entre PCs. Los proveedores de modelos configurados por
la persona pueden seguir usando sus servicios externos habituales; eso no cambia
el alcance local de la coordinación CAO.
