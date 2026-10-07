# Quickstart

Prerequisites: Linux/WSL amd64, Docker Engine >=25 activo y cliente Linux accesible; checkout completo; red para build.

```bash
./install.sh
./install.sh status
./install.sh stop
./install.sh start
```

URL http://127.0.0.1:9889/. Instalación existente migra el root personal por defecto. Nueva instalación genera issuer y acceso bootstrap privado; crear cuenta en el front. Opciones de root, nombre, workspace y build se documentan en --help. La migración crea backup y permite rollback; nunca publica tokens en consola. La verificación real incluye respuesta HTML, auth config, 401 sin token y Docker stop/start.
