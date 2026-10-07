# Instalación automática en Docker

La imagen `cao-personal:local` contiene el backend, el frontend compilado, el login,
el emisor local de tokens y los proveedores Claude/Codex. Un único contenedor
`cao-personal` sirve la aplicación en `http://127.0.0.1:9889/`.
Al detenerlo, la web y la API dejan de estar disponibles.

Desde la raíz del repositorio, en Linux o WSL:

```bash
./install.sh
./install.sh open
```

Requisitos: Python 3.11 o posterior en el host, Docker Engine Linux amd64 25+
con seccomp y acceso a `/var/run/docker.sock`. En Windows, Docker Desktop debe
estar arrancado y permitir montajes desde esta distribución WSL. La primera
instalación necesita Internet para construir las dependencias de la imagen.
No hay que instalar Node, Python del servidor ni compilar la web en el host.

El instalador construye la imagen con los lockfiles, inicializa una instalación
nueva o conserva la existente, comprueba una instancia temporal, detiene el
servicio anterior, realiza una copia privada y verifica web, API y autenticación.
Solo entonces deshabilita `cao-personal.service`. Un fallo durante el corte
recupera el despliegue anterior. Repetir el comando con la misma imagen y montajes
mantiene la instalación existente. `--no-build` reutiliza la imagen ya construida.

## Inicio de sesión y persistencia

Una instalación nueva abre «Crear cuenta» mediante un enlace privado temporal.
`./install.sh open` renueva ese enlace sin mostrar el token en la terminal.
Después se inicia sesión con usuario y contraseña de al menos 10 caracteres.
El login permanece conectado a la aplicación principal.

La configuración, la clave de firma, las cuentas y las sesiones SQLite permanecen
en `~/.local/share/cao-personal/`, montado dentro del contenedor en la misma ruta.
No se incluyen contraseñas, tokens, bases de datos ni credenciales en la imagen.
La migración conserva la identidad, los permisos y las sesiones existentes.
La carpeta debe seguir siendo privada, propiedad del mismo usuario Linux.

También se conservan un HOME del contenedor y los sockets tmux en directorios
hermanos `cao-personal-container-home` y `cao-personal-container-sockets`.
Las copias `cao-personal-backup-*` son privadas; el manifiesto
`docker-installation.json` registra la imagen y el despliegue anterior.
No borres esos directorios para actualizar o recrear el contenedor.

## Gestión y actualización

```bash
./install.sh status
./install.sh stop
./install.sh start
./install.sh                  # reconstruir y actualizar con comprobaciones
./install.sh rollback         # volver al despliegue anterior
./install.sh account-reset    # contraseña nueva mediante prompt oculto
```

El rollback cambia los procesos que sirven la aplicación y conserva los datos
actuales; no revoca sesiones ni restaura destructivamente una base de datos.
La copia privada queda disponible para recuperación manual. Las migraciones de
esquema incompatibles requieren su propio procedimiento; no se debe sustituir
esta imagen por una versión arbitraria antigua.

Tras volver al servicio anterior de WSL, `status`, `start` y `stop` gestionan ese
servicio. Para pasar de nuevo a Docker, ejecuta `./install.sh`.
Los contenedores anteriores quedan detenidos para permitir la recuperación.

## Proveedores, integraciones y espacios de trabajo

Los proveedores utilizan las suscripciones y configuraciones locales existentes,
montadas explícitamente. OpenCode conserva el ejecutable instalado en el host.
El repositorio actual está montado como espacio de trabajo; añade otros con
`--workspace /ruta/absoluta`. Estos montajes tienen acceso de escritura.

Playwright conserva su ruta local de Chromium, montada de solo lectura. Su ruta
de bibliotecas apunta a las dependencias Linux de la imagen, para evitar mezclar
versiones incompatibles de glibc con las del host. La configuración
MCP de Codex permanece en su carpeta original. Se conservan las skills globales
de `~/.agents` y, cuando está configurado, el montaje y la variable
`CLAUDE_OBSIDIAN_VAULT` del vault existente.

Si una integración MCP configurada necesita Windows `cmd.exe`, el instalador
crea `cao-windows-mcp.service`: un puente de entrada/salida por socket Unix
privado que solo admite los argumentos registrados en la configuración.
Roblox Studio sigue siendo una aplicación externa de Windows. El puente no
sirve frontend, backend ni puertos web. No se expone a la red.

Docker Work conserva su imagen mínima e inmutable de ejecución. Sus contenedores
se crean bajo demanda para tareas aisladas; la aplicación web completa sigue en
una sola imagen y un solo contenedor. El acceso al socket Docker permite gestionar
esos trabajadores, por lo que la instalación está destinada al propietario local.

## Otra instalación local

```bash
./install.sh --root /ruta/privada/cao --name otro-cao --port 19889
./install.sh status --root /ruta/privada/cao
```

El puerto siguiente se reserva para el emisor interno y no se publica. Los puertos
8079 y 8080 están excluidos para evitar conflicto con el proxy interno. El acceso
publicado se limita a `127.0.0.1`; el procedimiento no configura acceso remoto.
# Proyectos de solo lectura y admisión local

`scripts/docker_install.py` admite proyectos adicionales de solo lectura con
`--workspace-readonly RUTA`, además de los existentes `--workspace RUTA` de
lectura/escritura. Las rutas se resuelven antes de registrar los montajes;
solapamientos RO/RW se rechazan. El repositorio de CAO conserva su montaje
predeterminado. Un proyecto que se usa con worktrees necesita acceso de escritura.

Los montajes de proyecto nuevos llevan `purpose: "project"` y `readonly` en
el manifiesto. La recreación y rollback usan ese manifiesto. Los manifiestos
anteriores siguen siendo legibles y sus montajes no se cambian implícitamente.

El contenedor recibe `CAO_REGISTERED_PROJECTS` con los destinos de esos proyectos.
Se rechaza crear terminales fuera de ese registro, incluidos escapes por symlink.
Docker impone el permiso de escritura; el registro solo controla el directorio
inicial. Los agentes comparten el usuario del contenedor y sus permisos.

Véase [colaboración local](agent-collaboration.md) para delegación, integración
externa, aceptación privada y límites de la limpieza de procesos.


## Credencial de MCP renovable

La imagen actualizada usa el archivo privado renovable descrito en
[colaboración local](agent-collaboration.md#renovación-en-la-instalación-personal-local).
La emisión conserva la identidad de la instalación y no modifica las cuentas.
El runtime deja de reiniciar toda la aplicación cada 12 horas para rotar un JWT.
