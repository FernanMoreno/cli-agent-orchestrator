# Cierre: autorrenovación local MCP

Fecha: 2026-10-06. Resultado: implementado, reconstruido, desplegado y verificado.

## Problema y causa

Los procesos MCP recibían un JWT fijo al arrancar. Renovar las credenciales del emisor no cambiaba el entorno de un proceso abierto. El reinicio periódico de 12 horas evitaba parte del problema, pero interrumpía agentes y no renovaba su credencial durante el trabajo.

Se siguió `AI_WORKFLOW.md`: inspección del estado Git, análisis del grafo contrastado con fuente, Spec Kit, regresiones RED/GREEN, revisión de composición y verificación ejecutable. Se preservó el trabajo previo del repositorio y el selector de la especificación 008.

## Solución

1. El emisor personal publica `mcp-bearer.jwt` mediante escritura atómica, con permisos 0600 dentro de su directorio privado.
2. `CAO_AUTH_LOCAL_TOKEN_FILE` selecciona esa fuente. Los consumidores leen el archivo en cada petición, sin guardar el bearer en memoria entre llamadas.
3. La duración habitual es una hora; se renueva cinco minutos antes. La configuración privada admite 60–86400 segundos; para duraciones cortas el margen se limita a la mitad.
4. Si la publicación falla, el emisor reintenta después de cinco segundos. No reinicia agentes ni repite tareas. La API sigue rechazando cualquier JWT caducado.
5. Si el archivo configurado falta o es inseguro, no se utiliza el token estático del entorno. Un perfil que elige explícitamente un token estático deshabilita el archivo heredado; un archivo explícito mantiene su prioridad.
6. Claude, Codex y OpenCode reciben la variable sólo para el MCP de CAO. Los MCP externos conservan su configuración.
7. Se retiró el reinicio periódico de 12 horas del supervisor Docker y de la unidad systemd generada.

La lectura exige ruta absoluta, archivo regular del UID actual, directorio inmediato privado, permisos privados, un solo enlace y tamaño máximo de 16 KiB. Rechaza enlaces simbólicos, FIFO, directorios, contenido no ASCII y caracteres de control internos. La API conserva las comprobaciones de firma, expiración, issuer, audiencia y permisos. No se creó otra autoridad, endpoint de refresh ni tabla de persistencia.

## Pruebas y correcciones

| Comprobación | Resultado |
| --- | --- |
| Lector antes de implementar | 16 fallos esperados, 6 pruebas existentes correctas |
| Emisor antes de implementar | 9 fallos esperados |
| Override Codex con archivo heredado | 1 fallo esperado, 6 pruebas correctas; corregido en la construcción del comando |
| Suite final de seguridad, emisor, perfiles, MCP, contratos HTTP y Docker | **227 correctas**, salida 0, 71,30 s |
| Gate `project-composition-check caos` | **5 contratos conservados, 0 rotos**, salida 0 |
| Graphify AST focalizado final | 8 archivos, 217 nodos, 505 aristas; ningún archivo fallido |
| Construcción de imagen e instalación final | Salida 0 en ambas |

El comando de suite final cubrió `test/security/test_auth.py`, `test/security/test_local_token_file.py`, `test/scripts/test_personal_deployment.py`, `test/utils/test_collaboration_mcp_environment.py`, `test/utils/test_mcp_resolution.py`, `test/utils/test_opencode_config.py`, `test/mcp_server/test_utils.py`, `test/test_http_only_boundary.py` y `test/scripts/test_docker_install.py`, con `--no-cov -q -o addopts=''`.

La prueba de mutations verifica un único intento HTTP cuando falla la fuente: no hay replay automático. El análisis AST se guardó en evidencia privada, preservando el grafo global que ya tenía cambios ajenos.

## Prueba real en el proyecto del usuario

Proyecto: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`. Se utilizó el directorio real registrado, con instrucciones de revisión sin editarlo. La instalación personal real atendió las peticiones; los proveedores usaron su MCP nativo. Los adaptadores de aceptación reutilizaron el servidor existente y registraron evidencia; no sustituyeron respuestas de proveedores ni mensajes.

Los tres proveedores colaboraron durante dos rondas, con mensajes y recibos nuevos. La sesión conservó exactamente sus tres terminales. Se acortó temporalmente el TTL a 60 segundos para observar la caducidad real.

| Proveedor | Terminal | PID MCP | Inicio del proceso, ticks |
| --- | --- | --- | --- |
| Claude | `02901d14` | 3024 | 30529034 |
| Codex | `48ce7feb` | 3545 | 30530683 |
| OpenCode | `d7782085` | 3595 | 30530794 |

Al vencer el JWT original, la API devolvió **401** para ese JWT y **200** para el publicado después. Se conservaron identidad, scopes, IDs, PID y hora de inicio de los tres MCP. El contenedor no se recreó durante la prueba.

Con el mismo Claude abierto se ejecutaron tres llamadas adicionales, cada una solicitada expresamente:

- Fuente renovada: `list_native_children` correcto y recibo `RENEWAL_CLAUDE_OK`.
- Archivo sustituido temporalmente por un enlace simbólico: llamada rechazada y recibo `RENEWAL_DENIED`; exactamente una petición nativa, respuesta **403**.
- Archivo regular restaurado: llamada posterior correcta y recibo `RENEWAL_RECOVERED`, sin reiniciar ningún MCP.

El 403 negativo procede del control de origen existente: la petición nativa sin bearer tampoco lleva Origin. El primer intento del arnés esperaba incorrectamente 401 y falló en esa aserción. Se corrigió el arnés y la ejecución completa posterior terminó con `NATIVE_MCP_RENEWAL=PASS` y `COLLABORATION=PASS`, salida 0. La prueba directa del JWT caducado sí recibió 401. No se alteró el control de origen.

## Despliegue y conservación

Imagen final: `cao-personal:local`, ID `sha256:bee1adae3bc3c27fb753327febaf2f780150438e109299ca69d7311ca65c0af9`.

El instalador realizó la copia privada previa al corte: `/home/felni/.local/share/cao-personal-backup-20261006-015840-f6287f`. La comparación de cuentas y clave usa además la copia de antes de la primera actualización: `/home/felni/.local/share/cao-personal-backup-20261006-014653-d98dd2`.

Verificaciones después de instalar:

- Contenedor `cao-personal` saludable; health, HTML de interfaz y API con JWT firmado responden 200.
- TTL de aceptación eliminado de `deployment.json`: JWT publicado con duración **3600 s**, archivo **0600**.
- Filas de cuentas, clave de firma e identidad conservadas frente a la copia anterior.
- Hashes de los seis módulos/scripts de producción modificados coinciden entre fuente e imagen ejecutada.
- Terminales de ambas pruebas eliminados, consultas devuelven 404; perfiles temporales propios retirados.
- El arnés confirmó todos los archivos del proyecto sin cambios. `data/tasks.json` conserva SHA-256 `56e8dcaa7f8279fd8e9baca682808b84d707a66896e40b6a25444154ae3909ed`.

La recreación necesaria para instalar la imagen ocurrió después de terminar y retirar los agentes de aceptación. La prueba de continuidad corresponde a la renovación dentro del contenedor anterior, que ya incluía la solución; la imagen final añade la corrección específica del override estático de Codex, cubierta por regresión.

## Incidencias del entorno

`/tmp` estaba lleno por archivos ajenos a esta tarea. Se utilizó `/var/tmp/cao-mcp-renewal-felni` y evidencia privada persistente; no se eliminó ese contenido ajeno. Un TMPDIR dentro del HOME enlazado al contenedor provocó problemas de propiedad en el preflight; cambiarlo a `/var/tmp` resolvió la ejecución. Una copia manual mientras el servicio estaba activo fue rechazada por su bloqueo; se usaron las copias reales del instalador. Una invocación inicial de pytest refería un archivo inexistente y no ejecutó pruebas; se corrigió la lista antes de obtener las 227 pruebas finales.

## Evidencia y límites

Evidencia privada: `/home/felni/.local/share/cao-mcp-renewal-evidence/`, especialmente `native-final.log`, `native-renewal-results.json`, `regressions-final.log`, `composition-final.log`, `graph-final.log`, `build-final.log`, `install-final.log`, `deployment-final.json` e `increment.diff`. No se publican bearers ni contraseñas.

Se demostró renovación con expiración real corta y procesos abiertos. No se ejecutó una sesión continua de una hora o de doce horas; la configuración habitual se verificó mediante firma y claims. Las llamadas posteriores específicas de fallo/recuperación se hicieron con Claude; los tres proveedores participaron en las dos rondas reales y conservaron sus MCP. El override estático de Codex se cubrió por regresión de comando, sin una sesión adicional con ese perfil.

La renovación aquí implementada es la credencial del MCP de CAO. El login OAuth de cada proveedor conserva su propio ciclo. Esta entrega no afirma ausencia global de errores en todo el repositorio. No se hicieron commits ni publicaciones. Las tareas de esta especificación quedan cerradas dentro de estos límites.


## Aceptación ampliada completada el 2026-10-06

Los límites de la primera entrega sobre duración de prueba y proveedores adicionales se ampliaron: dos horas reales con 69 llamadas nativas de tres proveedores, dos renovaciones habituales y mismos MCP; fallo/recuperación con Codex y OpenCode; perfil explícito de Codex probado con archivo global inválido. Las pruebas, incidencias de invocación, limpieza y límites actuales están en [aceptacion-ampliada.md](aceptacion-ampliada.md). No se modificó código de producción en esta ampliación.
