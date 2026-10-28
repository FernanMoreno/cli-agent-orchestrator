# T197 · mensajes Claude actuales con perfil/filtro explícitos

**Preparación corregida y verificada; aceptación nativa pendiente.**

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
