# Investigación

Graphify enlaza `personal_deployment.environment`, `get_local_bearer` y consumidores MCP. Verificado en fuente: get_local_bearer lee sólo env; `_auth_headers` lo llama por petición; CAO_MCP_ENV_NAMES controla propagación; Codex usa env_vars para contexto heredado y una credencial privada para override explícito. `_write` ya implementa replace atómico privado. `serve` bloquea en process.wait; hilo con Event puede renovar mientras el hijo sigue vivo. Docker y unit tenían reinicios de 12h como workaround del JWT de 24h.

No se necesita duplicar issuer, SQLite, cola ni reintentos de mensajes.
