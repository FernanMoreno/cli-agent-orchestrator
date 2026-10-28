# T190 · Codex real antes y después del reinicio

**PASS acotado el2026-10-28**, fuente `090f01d8aba2f7891db3cff807720da0377c2fdd`,
505archivos fijados y Codex0.164.0. Modelo solicitado gpt-6-luna; no se atestigua
el modelo servido. [Recibo sin secretos](validation-t190-native-r5.json).

Dos llamadas reales de modelo a `send_message` terminan con éxito; dos filas
PostgreSQL y dos eventos AG-UI exactos, con una sustitución de API. La primera
identidad de fila coincide tras reiniciar y ambos terminales se conservan. El
recorrido mantiene los gates estrictos de cuerpo, entrega, receptor disponible,
correlación del turno emisor, auditoría/sello y limpieza. No exige ni acredita
un cuerpo ACK exacto del receptor. El paquete completo43assets se admite antes
de auth/servicios, y antes de cada trigger se observa el ejecutable real del
emisor, su etiqueta de terminal y su identidad UID/birth estable. No basta PATH.

Duración100,586s, mínimo muestreado9314MiB; límites180s/6144MiB intactos.
25identidades propias observadas y retiradas, APIs/tmux detenidos, contenedor y
red PostgreSQL propios ausentes, recursos ajenos conservados. Copias de auth
retiradas y originales intactos;505fuentes sin cambios. Ejecutado privado
archivado sin modificar y descriptor operativo re-cerrado tras comprobar cierre.

El cierre MCP usa el EOF explícito del diagnóstico (`harness_requested_eof`):
**no acredita cierre automático nativo**. Tampoco cierra `cao.work.message`,
autorización por operación, todos los perfiles/proveedores ni los gates globales.
T032 sigue aplazada por instrucción del usuario.

T194 corrigió la copia incompleta del paquete y validó el host sin modelo. El
éxito nuevo es compatible con ese mecanismo, pero no demuestra por sí solo
la ruta/invocación exacta del intento histórico R4, que no la conservó. Los
recibos fallidos anteriores y sus resultados permanecen intactos. No se cambió
Code Mode, permisos ni instalación global. Claude revisó el diff del lanzador;
la hipótesis de `/proc` no legible se comprobó y descartó con el mismo binario
en un control real sin credenciales/modelo/red antes de ejecutar.

Verificación previa:267pruebas distintas T194,37nuevas repetidas sobre los bytes
finales, black/isort, cinco contratos de arquitectura conservados y revisión
ClaudeGO. La revisión del lanzador y sus preflights son independientes de la
aceptación de los dos mensajes; las pruebas no se suman dos veces.
