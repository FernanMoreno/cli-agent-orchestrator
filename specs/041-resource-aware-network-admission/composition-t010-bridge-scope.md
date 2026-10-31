# T010 — identidad del participante Bridge

Resultado: PASS acotado al componente; T010 sigue abierto.

La factoría de confianza deriva una clave lógica por perfil/ámbito y una identidad mínima del proceso real. No carga claves peer ni cambia permisos, claims o autoridad. API y dos Bridges conservan revisiones independientes en PostgreSQL; revocar uno no modifica los demás. El binding sigue contrastando PID, namespaces, vista y destino. Un proceso nuevo conserva la clave lógica y cambia generación; se rechaza un objeto heredado cuyo PID ya no pertenece al proceso.

La revisión detectó misses concurrentes de lru_cache: dos generaciones podían invalidarse entre sí. Se reprodujo antes de serializar lookup/creación; el reset después de fork evita heredar el bloqueo y la caché del padre. El callback fork está revisado, sin aceptación física específica; la prueba de proceso nuevo ejecuta exec.

Validación final: 96 PASS, 0 fallos/errores/omisiones, 2 avisos, 47.30 s; fuente/soporte preservados. PostgreSQL real reutilizado: 0 clientes/bases/roles de prueba, contenedor y dueño intactos. Mypy acotado a un módulo y Black/isort de tres archivos correctos. Composición: 434 archivos, 1924 dependencias, 5 contratos mantenidos, 0 rotos. Revisión independiente Codex en modo lectura; hallazgo corregido y revalidado.

Graphify: 34 nodos y 151 enlaces incidentes contrastados contra código antes del cambio. Actualización diferida para preservar trabajo generado ajeno. Diff propio revisado. Conclusión durable registrada mediante transacción Obsidian save-20261031-scoped-resource-identity; lint estricto sin hallazgos.

Faltan el productor HTTP autorizado dentro de Bridge, la admisión de INPUT y el drenaje físico con reconexiones/epoch. No se acepta M1 ni capacidad real por este componente. No hay cambios de protocolo externo, migraciones ni SDK/UI. Véase [recibo](validation-t010-bridge-scope.json).
