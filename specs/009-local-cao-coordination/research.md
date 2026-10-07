# Investigación: Coordinación entre instancias CAO locales

**Feature**: [spec.md](spec.md)\
**Fecha**: 2026-10-04

## Decisión de alcance

- **Decisión**: tratar cada CAO independiente como un perfil/proceso distinto, con su propio directorio de datos, base de datos y puerto API.
- **Evidencia**: `constants.py` admite `CAO_HOME_DIR` y `CAO_API_PORT`; los valores predeterminados corresponden a un único perfil.
- **Motivo**: dos servidores que comparten base de datos o listener no están aislados como pares CAO, aunque sean procesos distintos.
- **Alternativa descartada**: reutilizar el perfil predeterminado en todos los procesos compartiría estado e identidad.

## Descubrimiento y localidad

- **Decisión**: usar un registro SQLite local compartido por el usuario en `Path.home()/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3` y comprobar cada instancia con un reto en su endpoint loopback. El directorio debe ser 0700 y la base de datos 0600.
- **Motivo**: descubre perfiles concurrentes sin cuentas, DNS, servicios alojados ni acceso desde la LAN. El registro propone candidatos; el reto confirma qué instancia viva respondió.
- **Alternativas descartadas**:
  - URL arbitraria introducida a mano: puede dirigir CAO a otro equipo y no demuestra que sea un par local.
  - Descubrimiento mDNS/LAN: amplía la superficie de red y puede alcanzar otros equipos.
  - Base de datos compartida dentro del proyecto: exige que todos los entornos monten la misma carpeta y convierte al proyecto en transporte.
- **Regla de localidad**: solo se aceptan peticiones loopback. No se confía en cabeceras de proxy ni en el registro sin una comprobación viva.
- **Límite confirmado por la persona**: el primer diseño coordina procesos dentro del mismo entorno de ejecución del sistema. Conectar Windows, WSL y Docker entre sus límites requiere otro adaptador y queda fuera de esta entrega.

## Identidad y autorización del par

- **Decisión**: guardar un UUID por perfil y crear un UUID nuevo para cada arranque del servidor.
- **Motivo**: la identidad del perfil se conserva; la generación de proceso permite descartar PID y puertos obsoletos.
- **Emparejamiento**: usar un código temporal de un solo uso y mostrar nombre, identidad, proyecto y permisos antes de confirmar.
- **Credencial**: crear una capacidad independiente por par y proyecto, revocable y limitada a acciones concretas. El receptor guarda un verificador; el perfil autorizado conserva su capacidad en datos privados.
- **Descartado**: copiar el token maestro de CAO. Da permisos más amplios y no expresa límites por proyecto.
- **Secretos**: no escribir credenciales en logs, telemetría, mensajes de error, prompts ni entorno del proveedor.

## Vinculación del proyecto

- **Decisión**: crear una identidad de proyecto a partir de la ruta canónica autorizada y, si existe, del directorio Git común.
- **Motivo**: dos bases CAO independientes no pueden comparar con seguridad los identificadores de memoria de cada perfil.
- **Comportamiento existente**: `project_marker.py` mantiene identidad de memoria opcional y no concede autoridad Work; no se reutiliza como permiso.
- **Reglas de rutas**: resolver enlaces simbólicos, exigir un directorio existente y evitar que el directorio solicitado salga del proyecto vinculado. En Git, comparar el directorio común real y la raíz, no solo la URL remota.

## Asignación, resultados y recuperación

- **Decisión**: reutilizar en `assignment_service.py` la clave de operación, el hash del contenido y la propiedad durable de asignaciones; reutilizar en `terminal_service.py` la creación y consulta de terminales.
- **Motivo**: las asignaciones ordinarias ya evitan que repetir una operación cree otro trabajador en silencio.
- **Brecha**: `FreshAssignmentRequest` modela el callback entre nodos remotos y rechaza worktrees. Los pares locales necesitan autorización propia y una API de estado; `target_host` arbitrario no es el modelo de confianza local.
- **Estados**: distinguir aceptada, en ejecución, completada, fallida, cancelada, interrumpida y pendiente de reconciliación. Un timeout tras posible admisión queda en reconciliación hasta consultar al destino.
- **Resultados**: consultar un recibo de tarea autenticado, basado en la asignación, el recibo de turno y la salida de terminal del destino. No poner la credencial del par en el entorno del agente.
- **Reintentos**: origen y destino usan la misma clave y hash canónico; reutilizar una clave con contenido distinto es conflicto.

## Cambios concurrentes

- **Decisión**: aislar en worktree las tareas CAO que modifican un repositorio Git y devolver la rama/ruta para revisión.
- **Motivo**: varios árboles de trabajo no sobrescriben entre sí los archivos editados.
- **Proyectos sin Git**: serializar las tareas de escritura mediante un lease atómico por proyecto en el registro SQLite compartido por los perfiles del mismo usuario. Un lease guardado solo en la base de un perfil no coordinaría CAO independientes. Si el proceso dueño cae, conservar el lease en estado interrumpido/reconciliación; no liberarlo solo por expirar un heartbeat.
- **Sin merge silencioso**: CAO informa solapamientos y conflictos. No integra automáticamente ramas distintas.
- **Evidencia**: las asignaciones locales ya envían `use_worktree` a la creación de terminal; la asignación nueva remota lo rechaza, por lo que el flujo entre pares debe usar explícitamente el camino local.

## Límite de confianza del proceso local

- **Evidencia**: `docs/tool-restrictions.md` indica que descubrir hermanos/grupos no crea una frontera de seguridad; `services/work_authority.py` declara que no proporciona sandbox del backend. Los proveedores CLI corren como procesos del usuario local.
- **Decisión aprobada por la persona**: tratar los CAO emparejados como procesos confiables del mismo usuario local. La capacidad del par limita las rutas de coordinación CAO y el proyecto seleccionado; no retira permisos de archivos que ya tenga el proceso del proveedor.
- **Implicación**: “permiso de par limitado al proyecto” no significa sandbox del sistema operativo. Si los pares deben considerarse no confiables entre sí, el lanzamiento aislado por SO debe ser un requisito explícito y requerirá otro diseño por plataforma/backend.

## Graphify y verificación del código

- La instantánea acotada `graphify-out/2026-10-02-spec008-structure/graph.json` conecta `submit_fresh_ordinary_assignment()` con `assign_fresh()` y muestra los usos de `assignment_service.py` desde las rutas API.
- Los hashes de la instantánea coinciden con el código actual de `assignment_service.py` y `utils/orchestration.py`; el hash de `api/main.py` no coincide, así que se verificó directamente su código actual en lugar de inferirlo de Graphify.
- La inspección directa confirma que `list_siblings` descubre terminales relacionados dentro de CAO, no otros procesos CAO.
- La instantánea principal `graphify-out/graph.json` es más antigua y no se usó para conclusiones sobre el código actual.
