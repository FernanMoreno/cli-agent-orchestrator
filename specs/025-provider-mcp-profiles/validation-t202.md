# T202 · Cierre EOF explícito de diagnóstico (preparación)

Se añade una opción estricta 0/1 para Claude, desactivada por defecto, que cierra
stdin mediante el transporte ya existente antes de pedir exit. Exige exactamente
dos llamadas exitosas y sello normal real dentro del mismo presupuesto compartido
de6s; no usa fallback ante fallo. El catálogo, perfil collaboration, filtro
send_message, permisos y comportamiento productivo permanecen iguales.

El recibo distingue petición, resultado cerrado de EOF y shutdown_mode;
native_automatic_shutdown_accepted siempre es false. T197 automático sigue abierto.
No acredita contrato externo Work, ACK exacto del receptor ni todos los perfiles.

Validación offline:19RED/1control→268PASS en113,22s en cinco módulos;44 controles
enfocados finales. Se corrigió una expectativa histórica de test Codex: con dos
resultados unknown la versión HEAD ya rechazaba EOF y no esperaba sello; se
preserva ese rechazo, sin rebajar el contrato. Sin PostgreSQL/modelos en esa suite.
Black/isort pasan. Composición PASS, contratos de arquitectura5/0. Revisión Claude
GO tras resolver dos ambigüedades del diagnóstico. El revisor no ejecutó pruebas.

Composición revisada: selector→config/env→stdio→sello→exit→cleanup→recibo. Plazo
único, tipos estrictos, modo predeterminado intacto, rechazo antes de efectos,
fallos parciales y compensación conservados. Graphify no indexa estos fixtures;
se verificaron los enlaces contra código sin regenerar los cambios ajenos del grafo.

Control nativo sin mensajes preparado y cerrado:11controles/19imports reales,
propietario PG no adquirido, sin credenciales preparadas. Se probará EOF con audit
vacío antes de gastar dos turnos de modelo. La preparación NO es aceptación nativa.
