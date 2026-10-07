# Renovación local MCP — plan de implementación

## Objetivo y arquitectura
Python 3.12, JWT RS256, archivos privados y Event/thread estándar. El único propietario de emisión sigue siendo `personal_deployment.serve`. Consumidores de HTTP usan `security.auth.get_local_bearer` por petición. Sin dependencias adicionales, tablas nuevas ni endpoints.

## Constitución
Código y pruebas son evidencia. Estado Git preservado; Spec Kit spec/plan/tasks; Graphify contrastado; regresiones RED/GREEN, integración real Docker y tres proveedores; composición y diff. Sin commits/push. La documentación del feature es la conclusión durable, no se duplica en el vault. Selector 008 conservado; comandos Spec Kit usan override temporal y restauran el selector.

## Opciones evaluadas
1. Archivo privado renovado por el emisor: elegido. Reutiliza identidad y escritura atómica; los MCP existentes leen el valor nuevo.
2. Endpoint de refresh: añade una autoridad y credencial adicional innecesaria para un único propietario local.
3. Reiniciar procesos para renovar env: no cumple continuidad de agentes abiertos.

## Fronteras y archivos
- `security/local_token.py`: lector puro de bearer privado; sin DB/tmux ni scripts. `security/auth.py`: usa lector cuando hay archivo configurado; scopes locales usan la misma fuente.
- `utils/mcp_resolution.py`: añade nombre de variable, desactiva archivo heredado si hay token explícito de perfil; verifica Codex por env_vars y overrides.
- `scripts/personal_deployment.py`: publicación usando `_write`, ciclo de renovación con Event; configuración TTL validada antes de abrir listeners; parada y join; env apunta al archivo; unit sin RuntimeMaxSec.
- `scripts/docker_personal_runtime.py`: elimina deadline que reiniciaba todo cada 12h.
- Pruebas nuevas de lector/propagación/ciclo; conserva pruebas anteriores.

## Secuencia
1. RED: archivo renovado vence env antiguo; rechazo de inseguridad y fallback; perfil explícito; publisher/ciclo.
2. GREEN: lector seguro, publicación y renovación única, propagación; comportamiento heredado cubierto.
3. Contratos/security/MCP y lifecycle; fallos de escritura conservan último archivo sin secretos.
4. Construcción y personal deployment: backup; TTL=60 sólo durante aceptación; tres proveedores y mismos MCP PIDs, JWT inicial expirado 401. Archivo fallido no fallback, reparación y petición posterior sin replay; restaurar TTL habitual.
5. Composición, diff, Graphify AST focalizado sin sobrescribir grafo ajeno, informe y selector intacto.

## Límites
La API valida firma/exp/iss/aud/scope; lector no cambia autorización. El archivo privado comparte UID del propietario, conforme al despliegue personal existente. Se conserva el token estático para compatibilidad cuando el archivo no está configurado. Ninguna llamada mutation HTTP se repite al recibir 401.
