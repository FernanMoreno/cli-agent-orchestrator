# T193 · nombre de ventana retenido y limpieza reintentable

## Defecto reproducido y corrección

PostgreSQL real y `ProviderManager` real reproducen una secuencia soportada:
DELETE detiene el pane antiguo, pero `cleanup=False` conserva proveedor/fila;
un hermano mantiene viva la sesión; `new_session=False` crea otra ventana con
la misma etiqueta; repetir el DELETE antiguo mata la sustituta. No requiere
IDs repetidos, SQL externo ni ventanas simultáneamente duplicadas. El control
falló con `old cleanup retry killed the replacement`.

La creación consulta ahora la etiqueta exacta dentro del bloqueo de sesión,
antes de resolver/publicar la encarnación o crear el backend. Una fila cuyo
runtime no está recuperado impide esa etiqueta; no se asignan recursos ni se
ejecuta un rollback por un nombre ambiguo. Otra etiqueta progresa, el hermano
sobrevive y la limpieza antigua puede reintentarse. Los tombstones recuperados
siguen conservados y pueden compartir etiqueta con una sustituta: su borrado
continúa siendo exacto, sin `kill_window`/`stop_pipe_pane` por nombre.

## PostgreSQL y migración

`has_unreclaimed_window_label` usa igualdad de sesión/ventana, `IS FALSE` y
`LIMIT 1`; no materializa hermanos. Core v3 añade un índice parcial no único
con la misma condición. Conserva las migraciones v1/v2, sus checksums, filas
y permisos. Dos controles RED exigieron v3 antes de implementarla; los
controles reales posteriores verifican actualización desde v2, repetición
idempotente, fila preservada e índice y rollback íntegro de una v3 fallida.
La prueba existente de denegación DDL al runtime se conserva.

La migración requiere el propietario de esquema antes de servir con el runtime
nuevo. `CREATE INDEX` dentro de la transacción puede bloquear escrituras al
construirse: su aplicación requiere el procedimiento de mantenimiento, no
una promoción automática en caliente. Aquí solo se migraron targets de pruebas
propios; no se aplicó a la base del operador ni se desplegó nada. La verificación
de esquema conserva su rechazo de versiones/catálogos incompatibles.

## Evidencia y composición

Primera selección:29PASS. Después, sobre los bytes formateados/revisados,
317PASS en165,43s en identidad/creación/borrado/tombstones/base de datos/
migración;104PASS adicionales en81,68s en sesiones, ciclo de proveedor y
retirada de fila/arranque, con `-m 'not e2e'` para incluir las tres integraciones
PostgreSQL/tmux. Son421pruebas distintas, sin sumar las29 preliminares. Dos
avisos Pydantic preexistentes por ejecución; ningún caso omitido en estos dos
runs finales. PostgreSQL fue seleccionado/verificado por sus fixtures y cerrado.

Black/isort PASS; arquitectura5contratos conservados/0rotos. Claude revisó el
diff congelado y sus vecinos en modo lectura: GO, sin atribuirse ejecución.
La consulta, índice, propagación de error antes de efectos, bloqueo, retención,
reintento y convivencia con tombstones se revisaron juntos. Graphify conserva
su snapshot87e2d5f; se verificaron las aristas contra fuente. No se sobrescriben
sus cambios ajenos; el nuevo helper es una dependencia servicio→cliente ya
permitida, cuya estructura se documenta aquí.

Los llamadores `reserved_window_name` de Work/agent-step usan `new_session=True`;
no cambian por esta guarda. Herdr devuelve la etiqueta solicitada, el pane tmux
devuelve la marca solicitada y la ventana tmux devuelve su nombre nativo tras
crearla con el nombre solicitado y protegerlo frente a renombrado.

## Límites

Bloqueo por proceso: no hay reserva atómica entre APIs independientes. Un
backend personalizado o renombrado externo que devuelva una etiqueta diferente
no queda probado. No se introduce rollback por etiqueta después de esa mutación.
La colisión con una etiqueta viva también se rechaza deliberadamente.

T192/T193 completan las dos fronteras locales recién reproducidas dentro de
T143, junto a las correcciones previas; no se marca todo T143: sus registros
anteriores también dejan abiertos generación/ABA, lecturas SQL inciertas,
trabajadores escapados y autoridad distribuida. T013 y la aceptación nativa
global siguen independientes. T190 continúa abierto y T032 aplazado.
