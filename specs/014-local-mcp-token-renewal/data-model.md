# Modelo

`mcp-bearer.jwt`: bearer RS256 del operador, archivo 0600 bajo root 0700, reemplazo atómico; sin registros nuevos en SQLite. Identidad/scopes proceden de deployment.json existente. El hilo mantiene únicamente expiración de la última publicación y Event de parada. `mcp_token_lifetime_seconds` es opcional: 3600 por defecto; integer entre 60 y 86400.
