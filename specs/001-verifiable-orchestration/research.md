# Investigación y decisiones

Fecha: 2026-09-22. Evidencia del workspace actual, incluidos cambios locales previos.
Las rutas son relativas a la raíz. No se consultaron servicios externos ni cuentas reales.

## D01. Migración incremental del ciclo de vida

**Decisión**: mantener entrypoints y proveedores, introduciendo un dominio común con
adaptadores de compatibilidad y un único escritor autoritativo por operación.
**Razón**: `clients/database.py:122` conserva recibo por terminal; `:175` conserva hijos;
`services/workflow_journal.py` conserva runs y steps. No son un historial universal.
**Alternativas**: reescribir el orquestador pierde compatibilidad; sólo añadir metadata
libre no sirve como autoridad ni evidencia privada. Se descartan ambas.

Prefijo de las rutas abreviadas: `src/cli_agent_orchestrator/`.

## D02. Recibo, resultado y evento son entidades distintas

**Decisión**: intención, transición y evento se confirman juntos; el efecto externo ocurre
fuera de la transacción. Un digest necesita además contenido recuperable validado.
**Razón**: recibo actual tiene tres fases y se reemplaza por terminal; `event_log_service.py:1`
es un buffer de 500 eventos/24 horas. Journal de workflow sí conserva eventos durables.
**Alternativas**: usar pantalla como prueba o confiar sólo en bus vivo no cubre reinicios.
No se promete exactamente una ejecución de efectos arbitrarios del proveedor.

## D03. Autoridad comprobable, separada de capacidades

**Decisión**: principal autenticado, grant versionado y allowlist de job preceden recursos.
El preflight combina proveedor, backend e instalación real; no usa compatibilidad por parejas.
**Razón**: `providers/catalog.py:38` describe flags; `api/main.py:2771` verifica instalación
de binarios. `services/terminal_service.py:1410` reconoce restricciones sólo por prompt.
`services/agent_step.py:633` documenta que `caller_id` no autentica al emisor.
**Alternativas**: perfiles, allowed_tools y caller_id como autoridad permitirían escalación.
No se anuncia sandbox de comandos/red a partir de una allowlist MCP.

## D04. Reservas y conservación antes de limpieza

**Decisión**: reservas atómicas por intento con fencing, aislamiento cuando corresponde
y resultado de diff/artefactos persistido antes de limpiar checkout.
**Razón**: `services/terminal_service.py:1254` declara cap best-effort concurrente;
`services/worktree_service.py:148` elimina worktree con `--force`, perdiendo cambios
sin commit aunque preserve commits no fusionados mediante borrado seguro de rama.
**Alternativas**: contar terminales no reserva recursos; crear worktree no acredita merge.
La nueva limpieza conserva o pone en cuarentena trabajo sin evidencia aceptada.

## D05. Contexto universal sin elevar memoria histórica a autoridad

**Decisión**: revisiones y snapshots independientes del run, persistidos antes de inyección.
Estados históricos se identifican como legacy; no reciben aprobación inventada.
**Razón**: `models/memory.py:118` carece de ciclo de revisión de afirmaciones.
`services/frozen_run_memory.py:83` sólo congela runs con manifest utilizable; YAML y
no-workflow mantienen rutas live. `terminal_service.py:198` distingue None y cadena vacía.
**Alternativas**: congelar por terminal con lecturas separadas permitiría divergencia entre hermanos.

## D06. Continuación cercada y decisiones durables

**Decisión**: importar paquete no ejecuta; verificar contrato, artefactos, grant y cese o
fencing del intento anterior. Registrar identidad efectiva y revisión examinada.
**Razón**: pausa de cuota puede reanudarse autónomamente (`terminal_service.py:2076`).
`approval_store.py:78` ya guarda decisiones write-once por plan; `approval_gate.py:128`
las aplica sólo a scripts y AG-UI mantiene algunas decisiones en memoria.
**Alternativas**: cambiar provider y reenviar prompt duplicaría trabajo o efectos.

## D07. Una autoridad por proyecto en primera modalidad multinodo

**Decisión**: múltiples clientes con escrituras condicionales contra autoridad única;
conflicto explícito, ACL, cursor de recuperación y tombstones.
**Razón**: `services/memory_gateway.py:19` selecciona transporte remoto, no replicación.
**Alternativas**: CRDT, consenso multinodo y escrituras offline necesitan garantías no
especificadas; no son prerrequisito para servir varios escritores con control de revisión.
Esta entrega no acredita alta disponibilidad.

## D08. Compatibilidad y migración desde el inicio

**Decisión**: migraciones versionadas verificadas, registros legacy legibles sin promoción
de garantías, restauración de DB junto a archivos y contratos con versión mayor explícita.
**Razón**: migraciones actuales de `clients/database.py` incluyen except con log debug;
la presencia de una función de migración no demuestra esquema operativo.
**Alternativas**: repetir CREATE/ALTER sin ledger dificulta diagnosticar estados parciales.

## D09. O01 y O04 necesitan cambios concretos, no duplicados

**Decisión**: contrato efectivo por step; edición con revisión esperada; propagar error
tipado desde script hasta settlement existente.
**Razón**: `manifest_freeze.py:60` explica seis campos omitidos a nivel run. La columna
`error_kind` existe (`database.py:1258`), YAML la escribe (`workflow_journal.py:514`),
pero `script_runner.py:850` llama a `settle_step(:1159)` sin parámetro de tipo de error.
**Alternativas**: otra columna o proveedor único inventado por run agravarían incoherencia.

## D10. Configuración y especificaciones históricas

**Decisión**: inventario de env vars y excepciones explícitas de enforcement; triage documental
por evidencia, conservando el relato histórico cuando sea útil.
**Razón**: `config_service.py:171` ya tiene ENV_REGISTRY; seguridad lee entorno intencionalmente.
Issue 568 aún dice no implementado, pero `docusaurus/package-lock.json` resuelve js-yaml 4.3.2
y `.github/workflows/ci.yml` contiene tabla adicional de findings. Es candidato a documentación
superada; requiere comprobar todos sus criterios antes de declararlo cerrado.
**Alternativas**: mover toda lectura de auth a settings o repetir bumps por etiquetas sería incorrecto.

## D11. Validación real y límites de autorización

**Decisión**: primero mock_cli y dependencias locales desechables; matriz real separada,
con resultado por escenario. Preparar upstream sin merge y release sin publicación.
**Razón**: `test/e2e/test_real_provider_matrix.py:719` ya cubre lifecycle parcial; cuota
puede causar skip y no demuestra continuidad. Referencias locales: main tiene 9 commits
propios y 5 exclusivos de upstream/main; no se hizo fetch.
**Alternativas**: matriz toda omitida no es release verificado; filtro de tests no es allowlist.

## D12. Fallo de baseline y gates locales

**Decisión**: aislar pruebas de manifest con repositorio controlado; conservar rechazo
de baseline incompleto. Diagnosticar configuración de composición antes de usarla como gate.
**Razón**: diagnóstico previo reprodujo `untracked hash budget exhausted` al leer
`graphify-out/graph.json`; archivos no versionados exceden presupuesto total de 64 MiB.
El helper retorna baseline no disponible y manifest_freeze retorna None.
`.ai/project-name` falta y `.importlinter` apunta a `cao_workflow`, no al paquete principal.
**Alternativas**: aumentar presupuesto o ignorar archivos sin contrato escondería el problema;
un gate que sólo comprueba un SDK auxiliar no prueba límites del orquestador.

## Conocimiento compartido

Decisión de esta fase: no escribir en Obsidian. Son decisiones propuestas del diseño,
ya preservadas en Spec Kit. Tras aceptación y verificación se seleccionarán únicamente
invariantes o causas recurrentes que deban sobrevivir cambios de sesión.
