# T192 · recreación tras limpieza diferida

Corrección compartida y acotada de T143. `_purge_stale_session_rows_for_recreate`
ignoraba el `False` de `delete_terminal_row`: el gestor retenía correctamente
el proveedor antiguo, pero la creación publicaba otra encarnación de sesión.
Una fila ordinaria retenida todavía puede dirigirse a etiquetas reutilizables.

Dos regresiones con PostgreSQL migrado y `ProviderManager` real reprodujeron
el defecto: ambas fallaron porque no se rechazaba la recreación. El backend y
la limpieza del adaptador se controlan; no hay modelo, CLI ni tmux real.
La corrección consume el rechazo dentro de la sección crítica ya existente,
antes de publicar fila/encarnación o crear el proveedor nuevo. La compensación
existente revierte únicamente el backend recién creado. Conserva la fila,
encarnación y propietario anteriores; el reintento tras limpieza satisfactoria
publica la nueva encarnación. Los tombstones intencionadamente retenidos siguen
la rama previa y no se eliminan.

Verificación:23pruebas de identidad de sesión PASS; después124PASS en73,02s,
con3deseleccionadas por la configuración existente y2avisos Pydantic, en:

```text
test/services/test_session_incarnation.py
test/services/test_session_service.py
test/services/test_provider_lifecycle.py
test/integration/test_provider_row_retirement.py
test/integration/test_provider_startup_retirement.py
```

Las23 están incluidas en las124; no se suman. Se ejecutaron después los tres
casos omitidos de `test_session_service.py` con `-m integration`:3PASS en42,59s,
con PostgreSQL y tmux reales;40casos ya cubiertos quedaron fuera de esa selección.
Total127pruebas distintas. Un intento previo de selección `-m integration` en
los dos archivos de `test/integration/` no seleccionó casos (exit5); no se cuenta
como prueba pasada ni fallo de producto. PostgreSQL real fue seleccionado/
verificado por el fixture y sus propietarios terminaron. Black/isort PASS;
arquitectura5contratos conservados/0rotos. Claude revisó el diff congelado y
sus vecinos en modo lectura: GO, sin atribuirse ejecución de pruebas.

Composición revisada: bloqueo de sesión → retirada del gestor → eliminación
SQL/sidecars → publicación, propagación de rechazo y rollback bajo el mismo
bloqueo, sin nuevos locks, eventos, esquemas ni almacenamiento. El grafo
87e2d5f identifica31aristas relacionadas; sus posiciones están desactualizadas
y se verificaron con fuente actual. No se regenera el grafo ajeno: no cambia
la estructura de dependencias.

Límites: un `False` por fila ya eliminada concurrentemente también rechaza
ese intento, de forma conservadora; puede requerir reintento. Las filas
anteriores retiradas satisfactoriamente en la misma purga no se restauran.
El escenario nuevo de rechazo usa backend controlado; las tres integraciones
con tmux real no inyectan esa deferral. No acredita cancelación de ese llamador, garantías distribuidas,
ABA de ID ni todo T143/T013. Tampoco cambia ni acepta T190 o los gates nativos
de OpenCode/Codex/Claude. T032 continúa aplazada.
