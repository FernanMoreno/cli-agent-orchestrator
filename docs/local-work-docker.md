# Demo local de Work con Docker

Este perfil ejecuta el worker ELF estático incluido en el repositorio. Sirve
para comprobar provisión, autenticación, admisión, despacho y limpieza en un
entorno local. No inicia un proveedor de modelos ni acredita un resultado Work
sin un recibo autenticado del receptor.

## Arranque

Requiere Docker accesible por un socket Unix local, un compilador C capaz de
crear un binario estático y las dependencias Python del proyecto instaladas en
`.venv`. Desde la raíz del repositorio:

```bash
.venv/bin/python scripts/local_work_docker_demo.py
```

El script crea una base SQLite y un checkout temporales, construye la imagen
raíz, genera un emisor JWKS en loopback y un bearer firmado, provisiona el
selector `local-docker` con los servicios internos y arranca `cao-server` sólo
en `127.0.0.1`. Imprime la ruta de un archivo privado `client.env` y el comando
`cao launch` que se ejecuta en otra terminal; usa exactamente esas instrucciones
mientras el servidor siga abierto. El archivo, el bearer y la base se eliminan
al detener el demo.

El ingreso exige `CAO_WORK_LAUNCH_MODE=required` en cliente y servidor y
`CAO_ENABLE_PUBLIC_WORK_INGRESS=true` en el servidor. El backend sólo se
registra con `CAO_WORK_DOCKER_LOCAL=1` y
`CAO_WORK_DOCKER_IMAGE_ID=sha256:<64 hex>`; el script fija el ID de imagen que
acaba de construir. `WORK_BACKENDS` sigue vacío si no se activa el perfil.

Un recibo `queued` confirma la admisión. El proceso estático puede quedar
`running/sent` porque no emite `task_received`; para comprobar su ejecución y
limpieza se usa la aceptación real de Docker:

```bash
CAO_T019_DOCKER_IMAGE_ID=sha256:<ID-de-imagen> \
  .venv/bin/python -m pytest -o addopts= -q \
  test/integration/t019/test_local_work_setup.py::test_local_setup_dispatches_bundled_worker_and_cleans_attempt
```

La prueba exige la salida exacta `CAO_LOCAL_DOCKER_WORKER_RAN` y verifica la
ausencia del contenedor y de la imagen del intento al terminar. La imagen base
permanece en el Docker local para repetición de pruebas.
