# Research

- Backend ya sirve wheel/web_ui (main.py:10364); build frontend antes de wheel.
- Browser auth exige peer loopback (security/auth.py:579) y setup 127.0.0.1 (api/browser_auth_routes.py:194): proxy interno con cabeceras forwarded eliminadas; publicación host loopback. Alternativa network=host descartada por dependencia de configuración Desktop.
- personal_deployment._config valida root propietario-only y host loopback; montar root absoluto con UID idéntico evita cambiar contrato.
- backup() requiere service.lock libre; stop antes del backup. restore() revoca cookies, no usar en migración.
- Work valida rootfs one-layer scratch y SHA de supervisor; preservar rootfs separado o stage worker-rootfs.
- CLI Docker WSL stub no funciona, socket activo. Instalado cliente Linux 29.8.1 desde distribución estática oficial contra daemon existente; no daemon nuevo.
- Claude 2.1.285/Codex 0.159.3 comprobados localmente y en npm. OpenCode host 2.0.18 no está en opencode-ai npm: montar ejecutable existente y comprobarlo dentro del contenedor, evitando sustitución silenciosa.
- Knowledge decision: esta decisión durable se mantiene en spec/plan; no copiar Graphify ni registros al vault.

## Executable discoveries and decisions

- Existing-account validation calls `_local_binding`, which imports `security.auth`
  and `constants` before `personal_deployment.serve` applies its environment.
  Select `CAO_HOME_DIR=root/cao` before that boundary in both admin containers and
  the runtime. This preserves the parent's scheduler DB and permits private backup.
- The native Docker client needs Buildx for the Work backend's stdin build archives.
  Copy the plugin from the same pinned Docker CLI stage into the application image;
  the existing Work backend requires no behavioral change.
- Host Playwright libraries require glibc 2.43 while the image has its own ABI.
  Preserve the configured library path using a link to the image's native libraries;
  mount Chromium read-only, rather than importing the host's incompatible libraries.
- Windows Studio MCP initializes successfully through the UID-checked Unix relay.
  Exact registered arguments are allowed; arbitrary commands are rejected. EOF,
  unresponsive children and descendants retaining output pipes have regression tests.
- A forced candidate-health failure proved compensation must wait for the previous
  API to become healthy after Docker start, rather than merely waiting for start's exit.
- Docker Desktop stopped responding during disposable acceptance. The host service
  remained available. Restarted Desktop and bounded Docker/systemctl operations;
  repeat acceptance after the daemon returns, before any personal-service cutover.
