# State and ownership

Root existente permanece intacto: issuer.pem/deployment.json/browser-auth.sqlite3/cao. No schema migration. Nuevo docker-installation.json privado registra image ID, container, runtime mounts y respaldo. Backup SQLite usa API backup e integrity_check, sin WAL suelto. HOME persistente container-home pertenece al mismo UID. Marcadores sólo contienen paths/IDs, nunca claves/password/token. Credenciales continúan en sus carpetas runtime; no entran en el contexto Docker.

Transición: host-running -> preflight-ok -> host-stopped -> backed-up -> container-started -> verified -> host-disabled. Fallo antes de corte deja host intacto. Fallo después de corte elimina únicamente candidato propio y reactiva servicio previo. Upgrade retiene contenedor anterior parado hasta verificar nuevo. Repetición reutiliza estado y contenedor sano. Sin restore() ni revocación implícita.
