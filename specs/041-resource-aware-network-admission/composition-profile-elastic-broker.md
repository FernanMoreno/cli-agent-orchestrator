# Perfiles Root y recibos del broker — revisión de composición

El compositor puede cargar un perfil estricto y construir un runtime inerte con comando Root explícito. Signer externo y trust público usan el mismo lector que la CLI. El worker ya financiado comprueba credenciales antes de su primer SQL y antes del issuer; el supplier vuelve a comprobar después según la retención poscommit existente. No se generan claves ni se modifican credenciales; el perfil no concede recursos. El selector Root del entorno no es autoridad y se rechazan capacidades/FD heredados.

La asignación elástica valida identidad y lease antes del envío remoto. Conserva solo una identidad segura para inspección si otro campo falla; ignora release_token y metadatos adicionales. Un HTTP200 o404 no prueba retirada. Solo un recibo tipado released=true/workload_present=false permite marcar worker_released; cualquier resultado ambiguo queda reconcile y no repite allocation/delivery.

## Fronteras e invariantes

Se revisaron lector privado/codec, modelos, compositor, worker/canal originales, activación firmada y custodia poscommit, JWT/lifespan, claim idempotente de asignación, broker fuente y persistencia de resultados. Archivos regulares privados, límite16KiB, permisos/UID y metadatos estables; base64 de32bytes canónica y match de key_id/clave pública. Constructores sin I/O; mismo trust para padre/hijo, callback opt-in con None conservando llamadas por defecto. No se interpreta PATH/PYTHONPATH como atestación de ejecutable: el entorno pertenece al compositor confiable.

Identidad del broker como segmento seguro de URL, strings estrictos sin coerción ni controles y booleanos exactos. Se preservan el productor actual de8hex y fixtures heredados worker-1. La respuesta del broker es un contrato del servicio, no un permiso de cómputo o prueba de cierre de créditos SQL. El helper sin consumidores de MCP y otros contratos ajenos no se presentan como corregidos por esta validación.

## Verificación

829 PASS,10 advertencias,61 módulos, cero fallos/errores/omisiones:748 IDs anteriores conservados+40 casos Root nuevos+16 de broker nuevos+25 casos existentes adicionales. Cinco recorridos Root CLI/PostgreSQL reales, uno nuevo con perfil/archivo externo de signer y comando -m. El cuerpo del recorrido HTTP directo anterior es AST-idéntico al helper en su rama directa; no se sustituye por el nuevo. Los resultados enfocados se solapan y no se suman.

Trece Python/siete fuentes son archivo-exactos. Tipos474 fuentes en frío, Black/isort13 y arquitectura477 archivos/2195 dependencias/5 contratos/0 rotos. PostgreSQL18.6, mismo contenedor y keeper conservados,0 clientes/0 bases/0 roles de prueba tras cierre; SQL5/resource7 intactos. Grafo privado parcial31 archivos/1946 nodos actualizado y verificado; doce Graphify ajenos preservados. [Validación](validation-profile-elastic-broker.json).

## Diagnósticos y límites

API de perfil ausente RED1; callback de preflight ausente RED3 y GREEN33. Signer aislado11 y combinación Root50+10 subpruebas. Broker RED10 fallos/1 control positivo, luego GREEN41 preservando readiness/proveedor/claims/replay y cuerpo real de retirada en el mock existente. Se mantienen los recibos fallidos. Un descriptor preparado no se cuenta como proceso ejecutado: se comprueban apertura y cierre reales.

PASS para estos dos componentes. Los presupuestos/JWT/grants del padre y del hijo son fixtures. Las llamadas HTTP del broker están simuladas y sus resultados durables usan PostgreSQL real; no se ejecutó un cluster Kubernetes ni aceptación de capacidad distribuida. No hay productor de financiación disjunta del issuer: Linux/política, copia de claims o un canal reader no autorizan migration SQL. H24D-05 y los diez gates originales permanecen abiertos. El perfil no cierra autoridad productiva, T015/T016, cuotas de proveedor, M1/M2/M3 ni SDK/UI restante.
