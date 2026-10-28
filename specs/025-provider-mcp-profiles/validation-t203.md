# T203 · Supervisión nativa: aceptación acotada

Implementado un caso estrecho para Codex: catálogo supervision completo, inspección
propia y de hijo directo, cancelación de tercero existente y activo denegada por el
servidor antes del POST de recuperación. El tercero usa MockCLI y un recibo pendiente
sembrado en PostgreSQL después de enviar el input: no es prueba de entrega de turno
por un proveedor real. API local sin autenticación; otros modos quedan separados.

El parser MCP valida el resultado contra el esquema de respuesta generado por el
CLI0.164 y exige JSON consistente, success booleano y denegación exacta. El esquema
primario admite isError null; success1/0 no equivale a true/false. Cuatro RED de esos
falsos positivos se corrigieron antes de usar el proveedor.

ApiObservations congela respuestas y parentescos antes de arrancar el MCP. El
middleware HTTP real registra sólo método/ID/acción/status en JSON privado0600.
Una lectura fallida no acredita autorización: se exige la ventana de cuatro GET
metadata del caller, uno del hijo y uno del tercero, todos200, sin POST nuevo y con
recibo/turno inalterados. Las cuatro lecturas del caller proceden de sus tres
comprobaciones de permisos y la inspección propia. Un POST desconocido con404 y
fila de auditoría exacta demuestra que el observador detecta intentos de acción.

Validación:44pruebas nuevas PASS/2avisos/37,15s y60regresiones actuales del servidor
PASS/72,68s; conjuntos distintos. Composición5contratos/0rotos PASS. Sin PostgreSQL,
auth o modelos en esas pruebas; los casos HTTP usan TestClient y archivos reales.

El controlador privado permanece cerrado y fue revisado por el lead tras el
borrador de Luna. Antes de adquirir recursos se validaron28imports reales,6firmas,
propietarioPG no entrado, reescritura del argv de API mediante subclasePopen con
/bin/true real/reaped, rechazo de comandos ajenos, módulo real de limpieza tmux y
selectores del entorno. La revisión detectó y corrigió el destino del worker,
el módulo pasado a limpieza tmux y una reasignación que descartaba el mínimo de
memoria. Se vacía sólo la copia privada de config.toml, como en T173.

Control previo separado: paquete completo43assets fijado, generación real del
esquema por Codex0.164 sin credenciales y en namespace sin red; invisibilidad de
credenciales probada dentro de ese namespace y proceso retirado. Confirma el
formato de respuesta, no una operación de supervisión ni aislamiento de una futura
ejecución autenticada. Ese control previo quedó separado del recorrido autenticado posterior.

T203 queda cerrado en el alcance descrito abajo; T007/T008 y los gates globales permanecen abiertos.
No se repite la matriz de21catálogos de T173. Los datos/IDs/generaciones, hashes de
credenciales y stdout nativo quedan privados; el recibo público tendrá sólo conteos,
categorías y pruebas booleanas. T032 y los jobs históricos siguen separados.

Revisión Claude final GO de código/controlador (previa a r2), condicionada a admisión de fuentes/runtime/memoria/observador antes del caso real. No es aceptación nativa. La fuente de esquema generada0.164 confirma el contrato de respuesta, incluido isError nullable.

## Resultado real, 2026-10-28

R2 aceptado sobre main0e40d9d1/520fuentes, Codex0.164.0/43assets,
100,333s y mínimo9695MiB (umbral6144). Catálogo supervision exacto11herramientas;
inspección propia e hijo directo correctas; tercero existente/activo consultado
con200, cancelación denegada por autoridad del servidor antes de cualquier POST
de recuperación, turno/recibo inalterados. Control positivo POST desconocido404
y auditoría HTTP real; ventana nativa de lecturas exacta verificada.

API, sesiones, tmux, procesos nativos/observador, PostgreSQL y red propios retirados;
contenedores y redes originales conservados, fuente y credenciales originales
intactas, copia privada retirada. Descriptor operativo recerrado después de
terminar; archivo ejecutado inmutable y pins comprobados antes de editar documentos.
[Recibo público](validation-t203-native-r2.json).

R1 se conserva FAILED por admisión antes de API/PG. La segunda admisión aisló
el falso bloqueo: cinco redes etiquetadas preexistentes sin contenedor. La fixture
siempre crea red única; se corrigió únicamente el controlador privado para rechazar
contenedores PostgreSQL ajenos, exigir una red propia nueva y conservar todas las
redes originales. Cuatro casos del predicado real pasan; imports/Popen/tmux
revalidados y Claude confirmó el cambio por lectura independiente. No se borraron
redes ajenas ni se relajó la comprobación de autorización.

Límites: transporte nativo app-server y MCP reales, API/PG reales, API local con
auth desactivada, actores MockCLI y recibo sembrado después del input. Cero turnos
de modelo solicitados. No acredita entrega nativa de trabajo, aislamiento de API
autenticada, todos los perfiles ni aceptación global.

Revisión final del recibo y alcance por Claude: GO, sin hallazgos bloqueantes.
Composición repetida tras el recorrido:5contratos conservados/0rotos.
