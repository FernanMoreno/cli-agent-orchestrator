# Revisión independiente de contratos y cobertura — T067/T068

No se encontraron P1/P2 en las fronteras revisadas. Las pruebas nuevas conservan los guardas de producción y no cambian el baseline ni excluyen código para obtener cobertura.

## Procesos y backend

Revisión release/plugins, lectura independiente: Docker intercepta solamente CLI/Popen nativos; el escenario durable mantiene SQLite, autoridad, snapshots, ELF publicado, credenciales y contenido real. Los casos de error comprueban propietarios exactos, imagen/metadatos, tamaños/truncamiento y cleanup incierto. Bubblewrap usa pipes, memfd sellados, hashes, sockets y pidfds de procesos propios. El arranque del proceso de prueba se acota y el padre limpia su hijo en finally; el cleanup de producción se observa después. **59 pruebas Bubblewrap y 78 Docker pasan**; los dos casos durables Docker se repiten tras ampliar aserciones (**2 passed**). La prueba positiva observa el contrato persistido actual: work running y attempt sent, sin inventar un recibo terminal que el adaptador de launch no produce.

Límite: son contratos locales de transporte, integridad y autoridad. No acreditan un daemon Docker desplegado ni un namespace Bubblewrap real.

## Pares, snapshots y gestor de memoria

Revisión data independiente, fuente contrastada: `database.list_all_terminals` publica `tmux_session` y el requester deriva `session_name` de esa misma identidad. T068 corrige la búsqueda del curator para consumir el campo real. Las pruebas crean terminales SQLite reales y preservan búsqueda, identidad por sesión y escritura de política; solo simulan tmux/proveedor/estado externos. Los snapshots usan migraciones, bytes, transacciones, permisos, rollback y corrupción reales. Los pares conservan identidades, firmas, grants, nonce, rutas y leases reales; solo simulan el transporte HTTP externo. **141 pruebas focalizadas pasan**, sin cambios del revisor. Log `/home/felni/tmp-cao015-types-data/t068-review-tests.txt`.

La unión de datos reales de diagnóstico y de pruebas focalizadas mide **87.0979647%** (63720/73159 líneas). Es una estimación para decidir repetir la selección global: incluye una suite con fallos y no acredita cierre. El cierre exige selección nueva sin fallos, cobertura propia y ambos informes presentes antes del ratchet.
