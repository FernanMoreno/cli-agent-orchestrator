# T197 · mensajes Claude actuales con perfil/filtro explícitos

**Preparación verificada; ejecución nativa fallida en el arranque del receptor.**

Se reutiliza el recorrido generic-message/API-restart, sin introducir otro
servicio ni modificar el comportamiento productivo. La preparación encontraba
un bloqueo de invocaciones en el observador, pero Claude no recibía perfil y
filtro explícitos de catálogo. El entry ahora selecciona collaboration con
enabled_tools=[send_message]; readiness exige exactamente esos campos y fija
los dos módulos de middleware en los bindings de fuente.

La composición con el constructor real de Claude expuso el comando legacy -B
como no canónico para el perfil. Se conserva el validador: Claude usa la
invocación canónica -m y mantiene PYTHONDONTWRITEBYTECODE=1. Codex/OpenCode
conservan sus argumentos y campos anteriores. El doble de respuesta del test
completo necesitó la anotación de retorno que requiere FastMCP; se corrigió
el doble sin cambiar la aserción ni el contrato del productor real.

TDD:9RED en una selección serial; final239PASS en84,55s, incluidos constructor
real -> archivo MCP -> env del perfil/filtro -> transporte FastMCP en memoria
-> catálogo exacto1 y ToolError de list_siblings antes del handler. Los ocho
controles nuevos cubren también perfil/filtro ausentes, otro perfil, filtro
vacío/parcial/duplicado y tipos inválidos. No es una llamada nativa denegada
ni prueba de autorización de todas las operaciones.

La primera reproducción paralela produjo además un error interno de cobertura
compartida; se archiva y no cuenta como reproducción limpia. Se repitió en
serie con COVERAGE_FILE privado, manteniendo opciones/umbrales. AI_WORKFLOW6.1
registra esa prevención. Dos selecciones amplias intermedias238PASS/1fallo
conservan el rechazo canónico y el doble incompleto; sólo239PASS es el cierre
offline final. Un preflight del observador también necesitó state_ref en su
namespace sintético tras añadir captura temprana; no fue defecto productivo.

Black/isort/diffPASS, arquitectura5contratos conservados/0rotos y revisión
ClaudeGO de fixtures y adaptador cerrado. Fronteras revisadas: config/grants
-> builder -> middleware de servidor -> dispatcher auditado -> API/PG/AG-UI
-> turno causal/cleanup. No hay nueva API/DB/migración ni dependencia; no
requiere Pact nuevo. El límite de cobertura Graphify de estas fixtures se
contrasta con la fuente y se preserva su trabajo ajeno.

Adaptador cerrado derivado de T190: fuente/intérprete/binario instalado fijados,
alias nativo privado hacia la misma versión2.1.303 de T195, OAuth sólo en copia
privada, propietario PG retenido y rechazo de propietarios externos no retenidos.
Antes de cada trigger, lectura de PID propio/identidad estable/cwd privado, tag
de terminal, observador/auditoría/fuente exactos y perfil/filtro del MCP real;
exige un único proceso. Siete controles con procesos Python reales y metadata
sintética, dos roundtrips0600, cuatro controles de privacidad del FIFO y limpieza
tras excepción/interrupción pasan sin Claude/modelo/auth/PG. Imports/firmas y
protocolo context-manager se comprueban sin adquirir servicios. El preflight
sintético no acredita la forma del proceso Claude: lo observará la ejecución.

Se mantienen180s/6144MiB y los6s de salida/sello Claude; el FIFO opcional es
diagnóstico posterior al fallo, no prueba de resultado ni criterio de éxito.
El lanzamiento desactiva autoactualizaciones por proceso con
[las variables oficiales](https://code.claude.com/docs/en/env-vars), sin tocar
instalación o login originales. No amplía permisos ni infiere Work, todas las
modalidades/perfiles o gates globales. T197/T011 siguen abiertos hasta sus
respectivos alcances reales; T032 continúa aplazada.

## Ejecución real r1 · 2026-10-28

Fuente `14a5903e44e1e13b9a55d55c0ba752a44a84ea0b`, Claude2.1.303,
modelo solicitado claude-sonnet-4-6 (modelo servido no atestiguado).
El POST del receptor terminó en HTTP500/provider_init_timeout a los146572ms;
el controlador duró171,48s. No se creó el emisor ni hubo llamadas MCP,
filas, eventos o sustitución de API. No se observó el filtro MCP nativo.
Es fallo de aceptación, no un resultado positivo de mensajes ni una prueba
contra los permisos del perfil.

Mínimo muestreado9871MiB frente a6144;505bindings sin cambios;13identidades
observadas retiradas; observador/API/tmux detenidos antes de cerrar al propietario
PostgreSQL. Contenedor/red exactos ausentes, contenedores originales conservados,
staging de credenciales retirado y credenciales del operador sin cambios.
Se conservan recibo y archivo ejecutado privados; descriptor operativo recerrado.
La compensación retiró el terminal antes del observador tardío: no se retuvo
viewport. PATH, diálogo inicial y detección de estado siguen siendo hipótesis.
T198 capturará el arranque antes de esa compensación, sin cambiar gates/plazos
ni lanzar mensajes. T197/T011 siguen abiertos.


## Ejecución real r2 · mensajes correctos, sello final fallido

Fuente `25a5094da1392d2f3ce4d8942ec73776b795e9fa`, Claude2.1.303/modelo solicitado Sonnet4.6,
modelo servido no atestiguado. Corregido T199, receptor y emisor arrancan.
Dos send_message de modelo finalizan success: dos filas exactas/delivered,
dos eventos AG-UI redactados, identidad de la primera fila y terminales
conservados tras un reinicio. Cada fase acredita turno causal COMPLETED y
recibo durable verificado. El MCP hijo real presenta collaboration y filtro
[send_message] antes de ambos triggers. Esto no es auditoría del catálogo
completo ni autoridad universal por operación/Work/ACK exacto de receptor.

Aceptación completa false: observer_audit. El POST de salida recibe HTTP200,
pero no se observa el sello normal en sus6s originales (exhausted_after_poll,
no_seal_at_poll_return). JSONL conserva las dos llamadas success sin marker de
finalización. HTTP200 acredita la solicitud, no salida física/EOF/retorno normal.
No hay viewport de esa fase ni stderr MCP retenido; SIGTERM, slash-command y
EOF son hipótesis, no causas demostradas. T200 observará el cierre sin repetir
mensajes de modelo y conservará plazo/criterios.

Controlador155,878s, mínimo9537MiB,507pins sin cambios,33identidades retiradas;
API/tmux/observador detenidos antes del owner PG, contenedor/red exactos ausentes,
otros contenedores y login originales intactos, staging auth retirado. Archivo
ejecutado inmutable y descriptor recerrado. T197/T011 siguen abiertos.
[Recibo](validation-t197-native-r2.json); r1 fallido permanece intacto.


## Latest source-bound attempt r5

The fresh production-close journey on fef8462e stopped at expired OAuth admission
before API/native/model startup. FAILED preflight, not an acceptance or a new
shutdown failure. Original receipts remain unchanged. T197/T210 stay open.
See [r5 projection](validation-t197-r5.json) and the
[closure and scope](validation-t210.md#original-full-journey-attempt-r5).

## Subsequent native acceptance r6

The current operator profile allowed the original journey to run on b22b85fa:
native result/cleanup PASS,2messages/rows/events,1restart, normalSDKseal within6s,
41identities retired. The parentFALSE from resolving the removed staging HOME
is preserved; independent read-only closure verifies physical removal and all
other original conditions, with Claude GO. T197/T210 close only for the explicit
production collaboration/owned-signal-stop mode. Default/Work/global gates stay
separate. [Evidence and limits](validation-t197-native-r6.md).
