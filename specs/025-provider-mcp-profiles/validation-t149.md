# T149 — reserva de identidad entre recibo de hijo y registro

2026-10-25. FR127/SC111; parte acotada de T143. Base main `233f19eba72b9fbc593b206a03cb3c55b4cb52ad`. [Fuentes y recibos](validation-t149.json).

## Reproducción

PostgreSQL real confirmó tres fallos: planificar sobre una fila existente sin hijo creaba un recibo ajeno; la creación real acababa asociando ese recibo al ID ocupado antes de fallar su INSERT; un creador raíz podía ocupar el ID de un hijo ya planificado. RED:3 fallos,27,88 s. Los mismos tres controles pasan tras corregir la publicación,30,44 s. El caso de servicio es no gestionado: el guardado temprano de identidad Work no interviene; el recibo ajeno inesperadamente presente consta en el RED conservado. La asociación incorrecta se comprobó contra la fila real del fixture, sin atribuirla a un ID supuesto.

## Corrección

La planificación y ambos escritores de terminales comparten admisión transaccional PostgreSQL por tabla/ID. La clave incluye el OID de la tabla `terminals` realmente resuelta por la conexión y el ID enlazado como parámetro. No depende de una ruta/configuración mutable para distinguir la misma tabla, ni de un bloqueo global o mapa permanente de claves. Una colisión del hash solo serializa claves ajenas; no concede permiso. La admisión se libera con commit/rollback/cierre.

Se exige READ COMMITTED antes de la espera: una instantánea repeatable-read anterior al commit ganador no demostraría ausencia. Con otro aislamiento se rechaza la operación antes de efectos. La planificación comprueba la ausencia de terminal bajo esa admisión y conserva su INSERT/commit y unicidad originales. Los recibos retenidos siguen reservando su ID, sin reutilización silenciosa.

El registro de un ID reservado exige el UUID exacto del recibo devuelto, padre, proveedor, perfil y estado `planned`. Se comprueba con bloqueo de fila antes de mutar encarnación de sesión, terminal o idempotencia y se mantiene hasta el commit. No es una nueva credencial ni un parámetro público de API. El estado planificado se valida; no se añade aquí una comprobación independiente del reloj de expiración. Una transición concurrente del recibo no puede completar entre validación y commit.

`terminal_service` captura el UUID y el éxito en el trabajador de planificación; el trabajador privado se drena ante cancelación repetida y el UUID se pasa al INSERT solo para ese hijo. La planificación sigue precediendo al backend. Una cancelación tras confirmar el recibo lo deja `planned` para reconciliación por lease, sin atribuirle una transición que no ocurrió ni iniciar el proveedor.

La auditoría encontró dos escritores de filas en producción: el cliente ORM y `remote_terminal_service.record_placement`. El segundo conserva el orden bloqueo de publicación remota→admisión compartida; rechaza cualquier reserva local de hijo sin adoptar su recibo antes de publicar filas o routing. Se mantiene el rechazo de colisiones locales y el replay remoto exacto.

## Validación y composición

19 casos en el nuevo archivo de admisión y2 controles remotos nuevos; junto a los2 controles remotos existentes:23 correctos,41,27 s. Se observa `pg_stat_activity` para probar la espera advisory efectiva; no basta con que un Future todavía no termine. Ambas ordenaciones entre escritores, rollback liberando la admisión, progreso de un ID independiente y un proceso Python separado están cubiertos. También se prueban UUID/padre/proveedor/perfil/estado incorrectos, recibo ausente, ausencia de efectos en encarnación/idempotencia, rechazo de aislamiento y cancelación repetida del planificador. Los dos órdenes de publicación remota conservan fila/recibo/routing y replay.

Regresión ampliada: **521 correctas,3 avisos,431,63 s**, cobertura enfocada configurada29 %, sin rebajar criterios. Incluye todo `test_terminal_service_full`, cliente de base de datos, turn recovery, T146–T148, KAS, canal remoto y servicios emparejados reales sin CLI/modelos. Siete fixtures que creaban fila antes de planificar se corrigieron a planificación→registro con UUID/padre; dos casos de recuperación preparan ahora el hijo antes de crear el terminal y su recibo de turno. Conservan las comprobaciones de cancelación, generaciones y resultados históricos. Seis sesiones simuladas declaran ausencia de recibo y READ COMMITTED; no se evita el guardado real para hacerlas pasar.

PostgreSQL propio con imagen/identidad verificadas;12.138 MiB iniciales frente al mínimo6.144. El propietario se mantuvo hasta salir pytest:442,260 s, cierre y ausencia exacta del contenedor comprobados.14 fuentes sin cambios durante la aceptación. Black/isort pasan. Arquitectura/Import Linter:5 contratos conservados,0 rotos.

Subsistemas revisados: planificación/registro SQL, servicio de creación/compensación, recibos y recuperación, publicación remota, sesión/encarnación/idempotencia y gestor. Invariantes: permiso por testigo exacto, exclusión antes de mutación, atomicidad del commit, instantáneas tras espera, orden de bloqueos, liberación por rollback y parada real ante cancelación. No cambia esquema ni interfaz pública; no procede un nuevo contrato Pact. Graphify se consultó sin regenerar sus12 cambios ajenos y se contrastaron los dos escritores con código.

Las revisiones Claude son de lectura, sin ejecución de pruebas: diagnóstico `0db8cc3f708443819518531b44ec34cc`, revisión de fuente `c5a44201ee754b3191c6429559156b63` y GO final del parche/controles `4ec3979ce7914026abf051304b9cbe54`. Luna no encontró inversión de bloqueos ni otro escritor de terminales en producción. Codex ejecutó y verificó las pruebas. Veredicto: **PASS WITH RISKS para T149; T143 permanece abierto**.

## Alcance pendiente

La garantía es de escritores conformes que comparten la tabla PostgreSQL: no autoriza SQL directo ni añade generación/CAS al borrado y reutilización del ID, reclamaciones entre bases distintas o borrado arbitrario del recibo. La retención histórica del UUID no es un mecanismo de reanudación. La cancelación del planificador conserva evidencia pendiente, no un runtime listo.

T143 mantiene sidecars en barridos, frescura/caché lista de Herdr, propiedad individual de archivos KAS en borrados posteriores, par de archivos no transaccional, cancelación de worktrees y trabajadores escapados. No se ejecutaron CLI/modelos reales ni se acredita aceptación nativa actual: hay que repinar lanzadores con toda la fuente antes de usarlos. Credenciales, instalaciones globales, QEMU, jobs históricos y cambios ajenos sin modificaciones.

Conocimiento durable: transacción `save-20261025-caos-native-child-registration-claim` aplicada a `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`. Lint estricto:0 incidencias,14 páginas. El archivo nuevo de pruebas se integra sin permiso de ejecución, conservando los cambios ajenos de Graphify.
