# Installation contract

CLI install (default), status, start, stop, rollback. Fallos devuelven no-cero; no se silencia Docker inaccesible. `--root` absoluto privado, `--name` nombre Docker validado, `--workspace` rutas existentes explícitas; `--no-build` requiere imagen disponible. Manifest atómico 0600. Publish 127.0.0.1 sólo. Account bootstrap se conserva en archivo privado y no se imprime. Sin ejecutar scripts externos remotos ni sudo implícito.

Proxy conserva Host/Origin/Authorization/Cookie; elimina Forwarded/X-Forwarded-For/X-Real-IP/X-Forwarded-Proto; WebSocket upgrade y SSE sin buffering. API e issuer siguen loopback internos; JWKS no se publica. Health HTTP y boundary no-auth prueban aplicación y frontend antes del éxito.
