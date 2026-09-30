# Evidencia de implementación — 004

Registro de la implementación inicial del 2026-09-30 y sus verificaciones posteriores. La instalación personal se activó después por petición del propietario; el cierre actual se documenta al final.

## Verificación ejecutada

| Comprobación | Resultado |
|---|---|
| Regresión Python: auth, persistencia browser, API, WS/AG-UI, Work/knowledge, tooling y compatibilidad | 268 passed, 114.60 s; repetición final de áreas modificadas: 79 passed, 96.33 s |
| MCP Work launch authority | 18 passed, 34.18 s |
| Vitest | 393 passed, 28 suites |
| TypeScript y build de producción | PASS; advertencia existente de bundle >500 kB |
| Playwright con Chromium real | 16 passed, 0 skipped, 1.4 min tras las correcciones finales |
| Canales tras corrección de outage inicial WS | 3 passed, 9.39 s |
| Import Linter / project-composition-check caos | PASS, 334 archivos, 1328 dependencias, 5 contratos mantenidos |

La primera ejecución ampliada coincidió con seis regresiones nuevas todavía en rojo de esquema SQLite. Se corrigió la causa y la ejecución completa posterior pasó sus 268 casos. No se contabiliza esa primera ejecución como gate aprobado.

La revisión adicional encontró reservas del limitador sin liberar ante fallos transitorios y clasificación incorrecta del cierre WebSocket inicial. Las regresiones reprodujeron ambos fallos; se corrigieron y el cierre confirmó 23 pruebas de servicio, 3 de canales y la ejecución conjunta final de 79 pruebas afectadas. Las contraseñas nuevas inválidas devuelven 422 antes de reservar capacidad. Black verificó nueve archivos de producción/pruebas; revisión diff --check sin errores.

## Infraestructura y escenarios

Chromium sirve el bundle de producción desde uvicorn autenticado y JWKS personal reales, con SQLite en disco. Se ejercitan login correcto/incorrecto, HttpOnly y ausencia de secretos en JS/storage/URL/422, ocho horas y cuatro renovaciones, dos pestañas, reinicio mediante reemplazo del intérprete Python, cierre/reapertura del proceso Chromium con el mismo perfil, cookie temporal, logout/all/password, red offline, orígenes ajenos, retroceso del reloj, idle/absolute, carreras con respuesta logout antigua, throttle concurrente, terminal tmux y WebSocket reales, SSE silencioso, backup/restore real del tooling y SQLite ocupado.

Los controles del reloj/restore/bloqueo son privados de la fixture por stdin: no se añade ninguna ruta de control al servidor de producción. No hay capturas, vídeo ni traces de formularios con secretos. La negativa a conservar cookies se inyecta en el límite de red del navegador retirando Set-Cookie; no se presenta como prueba de una política nativa de bloqueo de Chromium. El host necesitó una biblioteca de audio extraída en /tmp, sin modificación de paquetes del sistema.

## Límites de la evidencia

La compatibilidad Work crea antes del login una provisión, tres grants, un recibo task-received autenticado y al menos dos recibos de transición mediante servicios y SQLite reales. Verifica JWT RS256, misma autoridad/provisión y snapshot de once tablas idéntico tras cuatro renovaciones, logout y replay del recibo. La entrega del backend se representa mediante una observación de prueba; no acredita ejecución de Docker/ELF. El login no aprovisiona workers, no ejecuta proveedores de pago, no altera grants ni rota claves JWT. La restauración personal invalida sesiones antes de publicar destino; no acredita recuperación portable completa de un worker Work en otro host/ruta.

Los clientes bearer conservan sus regresiones API/MCP; no se ejercitó cada interfaz interactiva CLI/TUI ni cada IdP externo. El transporte soportado de esta entrega es el despliegue personal loopback 127.0.0.1 con origen exacto y bundle servido por CAO. Vite en otro puerto no habilita cookies por una reescritura de Origin.

## Reproducción

Ver [quickstart.md](quickstart.md). Resultados locales temporales: /tmp/spec004-final-python.log, /tmp/spec004-final-affected.log, /tmp/spec004-final-browser.log, /tmp/spec004-final-mcp.log, /tmp/spec004-final-composition.log, /tmp/spec004-web-final-tests.json y /tmp/spec004-web-final-build-log.txt. Este documento conserva la evidencia; esos ficheros no son artefactos durables del repositorio.

## Ajuste posterior: front integrado y despliegue real

El usuario autorizó conectar al token/instalación activa y pidió introducir la contraseña en el front. Se implementó FR-024: pantalla inicial dentro de la misma aplicación, autorizada por el JWT del operador existente. Rechaza acceso anónimo, otro subject/scopes/origen y reactivación de cuentas deshabilitadas; revalida la identidad dentro del bloqueo antes de publicar datos. El token bootstrap se consume desde el enlace local y se elimina al confirmar la sesión cookie.

Verificación fresca: 83 regresiones Python pasaron en 70.75 s; 14 contratos finales de setup en 12.52 s; 398 pruebas Vitest; build/TypeScript aprobado (bundle index-DgmqY4YK.js); 17 casos Chromium en 1.9 min y repetición del setup con el bundle final, 1 caso en 41.8 s. Cinco contratos de composición mantenidos. Revisión independiente sin hallazgos bloqueantes. Los números de pruebas se solapan y no se suman como casos únicos.

Instalación real: wheel y helper instalados en ~/.local/share/cao-personal-runtime; servicio cao-personal.service activo en 127.0.0.1:9889. Copia privada de recuperación: ~/.local/share/cao-personal-backups/browser-login-20260930-213533 (runtime previo y backup consistente de estado). Se verificaron la igualdad de la clave JWT y issuer/subject/puertos/image_id previos, GET protegido con bearer 200, sin bearer 401 y el bundle servido. Chromium verificó en la instalación activa el formulario para felni, ausencia del dashboard antes de completar setup y retirada del fragmento de autorización. Se abrió el enlace del propietario en su navegador; su contraseña se introduce sólo ahí.

Límite observado durante esta fase, corregido en el cierre posterior: un error de fsync del directorio después de reemplazar la configuración habilitada devolvía 503 y podía requerir reiniciar el servicio. No ampliaba autoridad ni permitía otra cuenta. Los artefactos temporales de esta comprobación son /tmp/spec004-setup-final-python.log, /tmp/spec004-setup-final-contract.log, /tmp/spec004-setup-final-browser.log, /tmp/spec004-setup-final-bundle-browser.log, /tmp/spec004-setup-composition.log y /tmp/cao-browser-setup-final-tests.json.


## Mínimo de contraseña solicitado: 10

Formulario y todas las verificaciones de alta/login/cambio/reset ajustadas a10–128 caracteres. Regresión del flujo completo primero falló con10 y después pasó; nueve caracteres rechazados por formulario, hashing y setup API. Comprobaciones:44 pruebas Python,399 Vitest, TypeScript/build,1 caso Chromium de setup/reinicio/logout/login con contraseña de exactamente10 y composición5 contratos mantenidos. Instalado en el runtime activo; hash/verificación de10 y rechazo de9 comprobados allí, bundle index-hOu3eHPe.js servido. Sin cambios de cuentas, claves, hashes existentes ni revocación de sesiones. Logs en /tmp/cao-password-10-{python,browser,composition,build}.log y /tmp/cao-password-10-web.json.


## Dos vistas de acceso

Preferencia confirmada del propietario: Iniciar sesión inicial y Crear cuenta, con pestañas y enlaces en ambos sentidos. Las pestañas deshabilitan navegación mientras hay una petición y desmontar un formulario limpia sus contraseñas. La vista de creación en una instalación configurada informa de la cuenta existente, sin exponer registro adicional. Dashboard conserva sus gates y autenticación cookie.

Regresiones iniciales de navegación3/3 fallaron antes del cambio; después12 pruebas enfocadas y402 Vitest totales pasaron. TypeScript/build aprobado,17 Chromium en2.0 min y composición5 contratos PASS. Wheel instalado en runtime activo, bundle index-CQTiAD1n.js servido y navegador del propietario abierto. Chromium comprobó allí las dos pestañas, enlace de alta, regreso al login, retirada del fragmento y mínimo10. Logs /tmp/cao-auth-views-{tests,build,browser,composition}.log; JSON /tmp/cao-auth-views-web.json.


## Cierre solicitado: interfaz, publicación y Docker real (2026-10-01)

Los textos visibles del acceso, las sesiones y sus errores están en español. Ante `setup_publication_uncertain`, el formulario bloquea otra creación y ofrece «Ir a iniciar sesión»: sólo consulta configuración y sesión mediante GET. La configuración válida habilita el login; una publicación inválida mantiene el acceso cookie bloqueado.

Se reprodujo un fallo real del segundo fsync de directorio, después de ejecutar os.replace, con publicación válida, identidad alterada y JSON malformado. La API conserva el 503 de publicación incierta, no emite cookie y reconcilia la configuración mediante la misma validación del arranque; no hace falta reiniciar para una publicación válida. Backend afectado:78 pruebas aprobadas; repetición final de los tres fallos:3 aprobadas. Logs /tmp/cao-auth-durability-check.log y /tmp/cao-auth-durability-final-rename.log.

La primera aceptación Docker del checkout completo usó worker ELF estático, JWT verificado contra JWKS HTTP y acceso web exclusivamente cookie. Provisionó antes del login, renovó tres veces y cerró la sesión mientras el worker estaba ejecutándose. El resultado terminó correctamente y quedaron exactamente un job, work, attempt, resultado, recibo y provisión, y tres grants. Sin doble entrega ni cambio de autoridad. Resultado:1 passed,0 skipped,28.23s. Esta ejecución incluía la composición managed Work de cambios anteriores que no forma parte del commit004. El límite Docker de la prueba anterior de SQLite quedó cubierto por esta ejecución adicional; la restauración portable entre hosts sigue fuera de esta evidencia.

Aceptación reproducible e independiente de esos cambios anteriores: `CAO_BROWSER_DOCKER_ACCEPTANCE=1 CAO_BROWSER_DOCKER_PROOF=/tmp/spec004-browser-docker-proof.json uv run pytest -o addopts= --no-cov -q test/integration/test_browser_auth_docker.py`. Requiere Docker y la imagen local aprobada; la fixture limpia únicamente su propio contenedor. La evidencia de la primera ejecución se conserva en /tmp/spec004-browser-docker-test.log y /tmp/spec004-browser-docker-proof.json. El test entregado usa /work-launches y gateway del baseline, ejecuta Docker real y asienta su stdout mediante WorkService.settle_attempt y validador real del servidor; no acredita transporte MCP task_received/submit_result en el commit aislado.

Frontend final:404 Vitest aprobadas; TypeScript/build aprobado; bundle index-CosnHpUn.js. Logs /tmp/auth-spanish-final-vitest.json y /tmp/auth-spanish-final-build.log. Composición:335 archivos,1332 dependencias,5 contratos mantenidos,0 rotos (/tmp/cao-auth-final-composition.log). Los recuentos de distintas ejecuciones se solapan y no se suman.

Cierre de regresiones:126 pruebas Python aprobadas en116.16s (/tmp/cao-auth-close-python.log);17 casos Chromium aprobados en1.9min (/tmp/cao-auth-final-e2e.log). Instalación personal actualizada con wheel final y helper; servicio activo, bundle index-CosnHpUn.js, una cuenta y una sesión conservadas. La clave JWT y deployment.json mantienen sus hashes previos. MCP Playwright verificó en un contexto limpio la pantalla española, las dos pestañas, contraseña vacía y ausencia de fragmento en la URL. No se solicitó, capturó ni modificó la contraseña del propietario.

Último ajuste de recuperación: el fallo real del primer fsync de directorio publica sólo el marcador pendiente; todavía no crea cuenta. GET confirma bearer/setup disponible y permite otro envío explícito sin replay automático.19 contratos backend de setup aprobados en17.25s y9 pruebas frontend enfocadas aprobadas, con regresión roja previa de la instancia retenida. Frontend completo final:405/405 aprobadas,0 omitidas (/tmp/cao-auth-close-final-vitest.json); build final index-1WGJJD8i.js, instalado y servido por el runtime preservando cuenta/sesión/clave/configuración. Gate final de composición:5 contratos mantenidos (/tmp/cao-auth-close-composition.log).

Prueba Docker reproducible del commit aislado:1 passed,0 skipped,52.20s (/tmp/spec004-browser-docker-launch-test.log; /tmp/spec004-browser-docker-launch-proof.json). Verifica cookie202, contenedor Running incluso después del logout,3 renovaciones, salida ELF exacta, bytes validados y almacenados,1 job/grant/work/attempt/result/provisión y ninguna entrega duplicada. El baseline requiere un recibo autenticado para aceptar el resultado: por ello conserva state=running,attempt=sent,accepted_result_id=null. No se falsifica ese recibo ni se añade la composición managed ajena; esta prueba acredita ejecución y conservación de datos, y su límite de aceptación final queda explícito. La ejecución histórica managed del checkout completo descrita arriba sí obtuvo succeeded.

Entrega aislada revisada en snapshot exportado del índice temporal:238 pruebas Python de auth/API/canales/Work/tooling aprobadas; repetición final19 setup más1 Docker real aprobadas en39.24s;367 Vitest del baseline aprobadas y9 de setup tras el último ajuste; TypeScript/build aprobado;17 Chromium del snapshot aprobados en1.6min. Los tests del checkout con Plugins y otras modificaciones anteriores no se incorporan a la entrega auth004. Sólo se incluyen los prerrequisitos del tooling personal/local Docker que necesita esta función; main/App/api conservan únicamente sus cambios de integración auth.
