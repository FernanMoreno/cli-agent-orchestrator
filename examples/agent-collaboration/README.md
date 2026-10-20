# Demos de colaboración de agentes

Dos aplicaciones pequeñas archivadas como ejemplos reproducibles de contratos y colaboración entre agentes. Son programas independientes: sus servidores no importan ni arrancan el runtime de CAO.

- [Lista de tareas](tasks/README.md): API Python estándar y frontend vanilla; creación y finalización de tareas.
- [Recuperación de turnos](turn-recovery/README.md): API y frontend de notas con edición, estados, filtros y operaciones de borrado.

Cada ejemplo conserva su contrato, código, pruebas y capturas. Los registros de colaboración/auditoría son históricos y no acreditan aceptación actual. Los archivos originales de estado mutable se excluyeron sin leerlos; cada demo tiene un seed `[]` limpio y documenta que el estado de ejecución se crea localmente y está ignorado por Git.

Ejecuta y prueba cada demo desde su propia carpeta siguiendo su README. Se usa Python 3.10 o posterior y Node.js 18 o posterior para la prueba frontend; no se requieren paquetes npm.
