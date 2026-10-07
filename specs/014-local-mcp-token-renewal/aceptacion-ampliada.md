# Aceptación ampliada de renovación local MCP

Fecha: 2026-10-06. Estado: **PASS**. Las tres comprobaciones pendientes están completadas.

## Resultados ya verificados

- Codex `6307ebdb` y OpenCode `8d137cf3`: llamada nativa correcta, archivo sustituido por enlace simbólico rechazado con una única petición 403, archivo restaurado y nueva llamada correcta. Sin fallback ni cambio de proceso MCP.
- Codex con perfil de token explícito, terminal `8ad82c94`: credencial elegida confirmada mediante hash en el entorno del MCP, `CAO_AUTH_LOCAL_TOKEN_FILE` vacío, llamada nativa correcta aun con archivo global inválido. Mismo PID 13911 y starttime 32968956 durante la prueba. Sesión y perfil específicos retirados después.
- Claude renovó automáticamente su login OAuth mediante una llamada real. El preflight inicial detectó correctamente el token OAuth vencido; no se cambiaron fechas ni credenciales manualmente.
- La primera llamada del Codex explícito añadió `terminal_id` a una herramienta sin argumentos y falló antes de llegar a la API. Se solicitó una nueva comprobación con `{}`; esa llamada y la comprobación del archivo inválido pasaron. Se registra como error de invocación del agente, sin ocultarlo ni modificar la firma de la herramienta.

## Sesión prolongada

Inicio: 2026-10-06 06:42:15 UTC. Duración mínima requerida: 7200 segundos reales. Configuración habitual conservada: JWT de 3600 s y margen de renovación de 300 s. El runner verifica firma/autorización mediante la API, expiración del JWT original, identidad, publicaciones distintas, salud, pertenencia de sesión y PID/starttime de los MCP. Cada cinco ciclos solicita nuevas llamadas nativas a los tres proveedores; no repite peticiones fallidas automáticamente.

Sesión: `cao-demo-collab-c703229f`.

| Proveedor | Terminal | PID MCP | Starttime |
| --- | --- | --- | --- |
| Claude | `7ae681ef` | 9819 | 32936753 |
| Codex | `6307ebdb` | 10370 | 32938468 |
| OpenCode | `8d137cf3` | 10319 | 32938374 |

Evidencia privada: `/home/felni/.local/share/cao-mcp-renewal-evidence/followup-native.log` y `followup-results.json`. La ejecución final terminó con `FOLLOWUP_TWO_HOUR_SOAK=PASS` y `COLLABORATION=PASS`, salida 0. El estado temporal propio ya fue retirado.


## Resultado de las dos horas

- Intervalo real: **06:42:15–08:42:42 UTC**, 2026-10-06; **7227,46 segundos** (dos horas y 27 segundos). No se aceleró el reloj ni se redujo el TTL.
- **111 comprobaciones** de salud, autorización, identidad, pertenencia de sesión y procesos.
- **69 llamadas nativas correctas durante la sesión prolongada:** 23 con Claude, 23 con Codex y 23 con OpenCode. Se exigió resultado real de herramienta, recibo verificado del turno actual y exactamente una petición HTTP correspondiente por llamada.
- **Tres credenciales sucesivas**, es decir, dos renovaciones observadas. La credencial original fue rechazada con **401** al terminar; las credenciales vigentes respondieron **200**.
- Mismos IDs, PID y starttime de los tres MCP en las comprobaciones periódicas. El contenedor tampoco se recreó durante la prueba.
- Las dos rondas iniciales de colaboración tuvieron mensajes y recibos nuevos de los dos trabajadores, con síntesis de Claude y exactamente los mismos tres terminales.

Instantes de emisión observados: 06:23:45 UTC, 07:18:45 UTC, 08:13:45 UTC. La primera credencial ya existía al iniciar la sesión; el emisor personal publicó las dos siguientes con su configuración habitual.

## Verificación final y limpieza

`followup-cleanup.json` terminó en PASS, salida 0:

- Sesiones de aceptación retiradas; consultas a los cuatro terminales propios devuelven 404.
- Ningún proceso con esos IDs permanece en el contenedor; perfiles temporales propios ausentes y archivo retenido de prueba ausente.
- Archivo de credencial regular con permisos 0600; TTL normal **3600 s**, firma y claims válidos.
- API firmada, health e interfaz responden 200.
- Clave de firma y filas de cuentas iguales a la copia privada anterior.
- Manifest de archivos fuente y datos del proyecto real sin cambios; se excluyen cachés `__pycache__` y `.pyc`, como en la aceptación inicial. `data/tasks.json` mantiene SHA-256 `56e8dcaa7f8279fd8e9baca682808b84d707a66896e40b6a25444154ae3909ed`.
- Los seis módulos/scripts de producción relevantes conservan hashes iguales entre fuente e imagen ejecutada.
- Selector de Spec Kit 008 preservado. No hubo cambios de código de producción, commits ni nuevo despliegue en esta ampliación.

Regresiones focalizadas: **25 correctas**, salida 0, 15,61 segundos (`test/security/test_local_token_file.py` y `test/utils/test_collaboration_mcp_environment.py`). La evidencia de la implementación y la suite anterior de 227 pruebas se conserva en [cierre.md](cierre.md); sus cuentas no se suman porque se solapan.

Gate de composición actualizado: `project-composition-check caos`, **5 contratos conservados, 0 rotos**, salida 0. Revisión final de los cambios de documentación completada.

## Evidencia y alcance

Archivos privados en `/home/felni/.local/share/cao-mcp-renewal-evidence/`:

- `followup-native.log`: colaboración real, turnos, marcadores de herramienta y salida final.
- `followup-results.json`: 111 comprobaciones, procesos, renovaciones, pruebas negativas y perfil explícito.
- `followup-cleanup.json`: conservación y retirada de estado propio.
- `followup-regressions.log`: regresiones focalizadas.
- `followup-composition.log`: gate de arquitectura y composición actualizado.
- `followup-project-manifest.json`: baseline de contenido usado para conservación durante la sesión prolongada.
- `followup-native.py` y `followup_probe.py`: adaptadores privados usados sobre la instalación real; no sustituyen respuestas de API ni proveedores.

Quedan cubiertos los tres límites indicados al pedir esta ampliación: duración de varias horas, fallo/recuperación con Codex y OpenCode, y token explícito de Codex en ejecución real. Esta aceptación demuestra una sesión de dos horas; no afirma una prueba de doce horas o varios días ni una auditoría global sin errores. El error inicial de argumentos de Codex y la renovación del login de Claude se conservan arriba para que el resultado sea revisable.
