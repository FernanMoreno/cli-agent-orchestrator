# Revisión de composición — renovación local MCP

Fecha: 2026-10-06. Resultado: aceptación cumplida según `cierre.md`.

| Frontera | Contrato e invariante | Evidencia |
| --- | --- | --- |
| Emisor personal / archivo | Un propietario bajo service.lock; JWT firmado con la identidad existente; reemplazo atómico privado | Publicación, TTL, permisos y preservación del archivo anterior cuando falla os.replace |
| Archivo / consumidor HTTP | Lectura por petición, sin caché ni fallback cuando se configura archivo | Reemplazo, inseguridad, desaparición y reparación; JWT anterior 401 y actual 200 |
| Perfil / proveedor / MCP | Variable sólo para CAO; elección explícita preservada | Tests de helper, override Codex y procesos nativos reales de tres proveedores |
| MCP / API | Firma, expiración, audiencia y scopes siguen en la API; ninguna mutación se repite | Tests HTTP-only, única llamada negativa y rechazo real 403 por origen al faltar bearer |
| Renovación / trabajo abierto | Publicación no crea ni reinicia trabajadores; misma sesión y procesos | Dos rondas, IDs/PID/starttime iguales, contenedor igual durante caducidad y recuperación |
| Fallo / recuperación | Emisor reintenta publicación en 5 s; no reenvía tareas; API rechaza credenciales vencidas | Tests de ciclo, conservación del archivo y llamada posterior real de Claude |
| Ciclo / parada | Event de parada y join del hilo; supervisor conserva detección de hijos caídos | Fuente y pruebas de lifecycle; instalación final saludable |
| Imagen / persistencia | Actualización conserva cuentas, clave y montajes | Backup del instalador, comparación SQL/bytes y hashes de fuente ejecutada |
| Compatibilidad | Auth deshabilitada y token estático existentes; sin migración de tablas ni nueva autoridad | Suite final 227 pruebas; contratos 5 conservados, 0 rotos |

Graphify se contrastó con los consumidores reales y se refrescó en evidencia privada. Se revisó el diff incremental frente a snapshots previos para conservar cambios ajenos. La fuente mantiene dependencia de security hacia un lector puro; no incorpora imports de scripts o persistencia al módulo de autorización. No hay transacción de base de datos ni eventos nuevos. La renovación cambia únicamente el material firmado disponible para peticiones futuras.

La garantía de atomicidad corresponde a lectores viendo archivo anterior o siguiente durante os.replace, con validación de ambos en la API. El UID propietario sigue siendo el límite de confianza del despliegue personal. Los logs de fallo no incluyen tokens ni contenido del archivo. Los límites de duración y de prueba nativa por proveedor se detallan en `cierre.md`.


## Ampliación de evidencia real

La aceptación adicional verifica la frontera perfil/Codex/MCP con una credencial explícita, el fallo cerrado y la reparación con Codex y OpenCode, y la renovación habitual durante dos horas con los tres MCP abiertos. Hubo 111 comprobaciones y 69 llamadas nativas correctas; contenedor y procesos conservados. Limpieza y conservación comprobadas después. Véase [aceptacion-ampliada.md](aceptacion-ampliada.md).
