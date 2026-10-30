# Research — 041

## Decisión: dueño común, no una nueva constante

Cambiar4por8 o solo ampliar `event_bus_max_queue_size` conserva el problema. La profundidad de cola no es ejecución y tres presupuestos por entrada suman consumo oculto. Se separa una política pura del dueño común de reservas para poder verificar presión y ciclo físico de vida independientemente del scheduler.

## Decisión: capas local, compartida y colocación

M1 utiliza observaciones frescas y mantiene los pendientes existentes. M2 aporta atomicidad entre nodos; las medidas de presión por sí solas no reservan capacidad. M3 reutiliza el intento durable de asignación elástica ordinaria y exige preparación además de lease de infraestructura. El registro local de peers no demuestra recursos libres; no se asume autoscaler existente.

## Decisión: incertidumbre contabilizada

No se usa expiración de una observación como prueba de liberación. Los awaiters y los hilos tienen vidas diferentes; una cancelación antes del inicio se puede liberar, después del inicio requiere fin físico. Se conserva ese principio para conexiones y nodos remotos.

## Decisión: costes y unidades explícitas

Los costes por operación son estimaciones configurables, no límites globales de agentes. Toda decisión identifica el recurso limitante y cobertura. Se rechazan datos imposibles en lugar de aplicar defaults silenciosos que acrediten recursos inexistentes. La aceptación física debe contrastar las estimaciones.

## Evidencia fuente

`inbox_service.py`: consumidor, barridos, exclusión, operaciones y drenaje. `legacy_memory_audit_repository.py`: sesiones físicas y autoridad, límite4/2s. `settings_service.py`: profundidad de cola y límite opcional de terminales. `elastic_assignment_service.py`: intención y broker; no capacidad genérica. `work_scheduler.py`: política durable de Work separada. Graphify:564nodos coincidentes/1031relaciones internas históricas contrastadas con esas fuentes. Artefactos Graphify ajenos preservados.

## T005 — Fronteras de jerarquía y márgenes por dominio

La revisión y cuatro regresiones reproducen tres fallos del primer observador: exigir archivos de control no-root en la raíz real; declarar completa una vista virtualizada; y combinar min(límites) con min(headroom) antes de reservar, perdiendo el margen de un dominio más grande ya presionado. Se preservan pares de límite/headroom por dominio y se calcula el margen antes de tomar mínimos. La observación no puede atestiguar por sí misma su namespace: por defecto es parcial y no aumenta admisión. Solo un bootstrap de confianza, vinculado a la encarnación y dominio reales, puede declarar una jerarquía completa y distinguir raíz host de raíz delegada. Esos hechos no pueden venir de mensajes, perfiles o contexto del modelo. La integración de esa prueba sigue enT006/T007.

Fuentes primarias verificadas: [interfaz cgroup v2 del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html) y [cgroup_namespaces(7)](https://man7.org/linux/man-pages/man7/cgroup_namespaces.7.html). Los archivos de límites indicados se documentan para grupos no-root; las vistas de pertenencia y montaje pueden estar virtualizadas. La existencia de valores locales no demuestra que todos los ancestros sean visibles.

## T006 — Política operativa separada de atestación

ConfigService no distingue operador y admite rutas desconocidas desde su CLI. Sus lectores genéricos pueden sustituir valores inválidos por defaults, lo que no sirve para presupuestos de seguridad. El MCP no tiene setter genérico y las rutas HTTP actuales solo cambian directorios de agentes/skills. Por ello la política nueva se carga como documento versionado y privado del operador, separado de settings.json, con cambio atómico tras validar todo y retención de última válida solo en el mismo ámbito. Un arranque inválido queda sin política. El documento solo contiene costes/márgenes/intervalos; no reservas, autorización, jerarquía o identidad. Esos estados y la autoridad siguen requiriendo PostgreSQL y un Principal verificado con permiso admin; la dependencia legacy de scopes default-off no basta.

La presión PostgreSQL es agregada del servidor alcanzado por un target, no una reserva ni prueba de identidad física del dominio. No cubre por sí sola límites de frontend de un pooler ni otros targets. M1 necesita mapeo de ámbito autorizado y M2 reservas compartidas; no se usará la identidad lógica de esquema/store como sustituto.

El lector privado comprueba el descriptor antes de leer; su apertura no bloqueante evita esperar por un escritor FIFO. El JSON rechaza campos repetidos en cualquier objeto. El exceso de anidamiento se clasifica como documento inválido y conserva la política anterior. La regresión usa20.000niveles dentro de64KiB: en el runtime CPython3.13,2.000niveles todavía se decodifican aunque el límite de recursión Python sea1.000. No se reduce un límite del intérprete para fabricar la reproducción.

Una sonda puede fallar después de abrir físicamente y antes de entregar un handle: tanto la construcción como __enter__ pueden ocultar un cierre fallido. Ese caso queda bloqueado sin nuevas aperturas ni autocancelación; cuando existe handle solo su testigo closed permite continuar. Un fallo del proveedor de destino anterior a la apertura no genera deuda física y puede recuperarse. T008 debe mantener un único dueño y no sustituirlo al recargar política para eludir esa incertidumbre; crear otro sampler no prueba retiro. El timeout de conexión existente sigue en10s y la consulta tiene su presupuesto local configurable,500ms por defecto; no se promete un timeout global del hilo ni se libera por cancelar su awaiter.

T007 requiere protección central de la tabla de atestaciones después del GRANT genérico de cada migración, además de comprobar privilegios efectivos de tabla/columna/PUBLIC en verify_runtime_role. No basta revocar DML una vez en su propia migración ni añadir una función SECURITY DEFINER accesible al rol runtime. La vinculación derivada del servidor incluirá generación, namespaces y digest de pertenencia/vista; una identidad lógica peer no prueba recursos físicos. La atestación acredita una declaración autorizada del operador, no verifica por sí sola ancestros invisibles.
