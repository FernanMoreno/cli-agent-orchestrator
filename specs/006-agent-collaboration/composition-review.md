# Composition Review: colaboración de agentes

**Fecha**: 2026-10-01. **Estado**: demo funcional verificada con intervención; feature pendiente de implementación y aceptación global.

## Superficie revisada

CAO original local `upstream/main` en `2fcc3efa`, fork, proveedores Claude/Codex/OpenCode, MCP, autenticación, tmux, inbox, instalación integrada, proyectos externos y lifecycle. No se han cambiado fuentes de runtime en esta fase.

Graphify leído mediante recorrido del grafo existente sin regenerarlo. Relaciones MCP → orchestration → API/inbox verificadas contra fuente; sus números de línea son anteriores al estado actual.

## Evidencia inicial

- Plan y contratos creados, 26 tareas en orden y enlaces locales verificados.
- `project-composition-check caos`: PASS, 5 contratos Import Linter conservados y 0 rotos. Este gate acredita arquitectura, no colaboración real completa.
- Instalación personal `cao-personal`: running/healthy al inicio; no recreada para la prueba.
- Versiones reales: Claude Code 2.1.285, Codex 0.159.3, OpenCode 2.0.18. Credenciales existentes comprobadas por presencia, sin incluir valores.
- Proyecto nuevo: `/mnt/c/users/ferna/onedrive/escritorio/cao-collaboration-demo-20261001-cf8017b2`.
- Instancia temporal `cao-collaboration-20261001-cf8017b2`, misma imagen que instalación personal; API CAO loopback 19881 y demo loopback 19981. Agentes en tmux del mismo contenedor. Estado y configuraciones aislados y privados.

## Fronteras e invariantes

- Identidad: terminal_id propio y caller_id conservado; bearer compartido del despliegue, sin afirmar aislamiento criptográfico por agente.
- MCP: perfiles privados de prueba reenvían conexión/auth explícitos; Codex mediante nombres en env_vars, sin bearer en argv. No constituye reparación de los perfiles generales.
- Mensajes: persistencia y entrega separadas; resultados verificables distintos de aceptación del mensaje. Recibos activos bloquean nuevas entradas con 409, conservando incertidumbre.
- Host: montaje exacto del proyecto, sin montar todo el Escritorio. API/frontend escritos por los agentes, no por el runner de control.
- Lifecycle: recursos temporales identificados para limpieza; no stop/restart de instalación personal ni servicios ajenos.

## Fallos reproducidos y ajustes temporales

1. OpenCode visible mediante docker exec, pero shell login de tmux no encontraba `opencode` (`not found`). Su montaje estaba en `/opt/cao/host-tools`, fuera del PATH efectivo de esa shell. Ajuste reversible solo en contenedor temporal: enlace en `/usr/local/bin/opencode`. La corrección persistente de instalación queda pendiente.
2. Perfil OpenCode presente en agent-store CAO pero no instalado como agente nativo; mostró `Agent not found`. Se ejecutó `cao install demo-frontend --provider opencode_cli` con CAO_HOME_DIR de prueba, escribiendo solo HOME privado de esa instancia. Comprobar/documentar este prerrequisito en el runner futuro.
3. Supervisor quedó esperando resultados sin emitir recibo final de turno: entrada posterior rechazada con 409. Una primera tarea acotada a delegación emitió recibo válido y pudo verificarse por la API. En el turno de espera siguiente volvió a omitirlo. No se forjaron recibos ni se desactivó su verificación; se separó integración en una tarea nueva acotada. La colaboración autónoma sin intervención sigue siendo una frontera pendiente.
4. Claude asignó frontend con ID placeholder aunque luego remitió el ID real por inbox; esos mensajes quedaron pendientes mientras OpenCode trabajaba. Hace falta pasar identidades concretas en la primera tarea, y comprobar el flujo de preguntas/respuestas en turnos breves.

## Resultados acreditados

Claude delegó a Codex y OpenCode mediante MCP/assign. Codex creó/revisó `api/server.py` y `CONTRACT.md`, envió mensajes al trabajador frontend y devolvió resultados al supervisor. OpenCode creó `frontend/index.html`, `styles.css`, `app.js` y envió resultados al supervisor por send_message sin destinatario explícito. Caller real confirmado en API.

- Claude `279d68ff` asignó a Codex `7024eb6a` y OpenCode `156cfb4e`; API, frontend y contrato fueron escritos por esos proveedores reales.
- Claude `92931d68`, en una sesión de integración acotada, ejecutó handoff a Codex para revisar el contrato y corregir el servido estático. HTTP `/`, `/styles.css`, `/app.js`, `/api/health` devolvió 200.
- Navegador Playwright detectó que OpenCode había fijado `http://localhost:8091` como origen, incompatible con el puerto publicado 19981. Otro turno de OpenCode `69762243` corrigió su frontend a rutas relativas. Desde la UI se creó `Prueba navegador CAO 20261001`, se completó y se recargó; checkbox marcado y API con `{id:4, completed:true}`. Captura `demo-preview.png` conservada dentro del proyecto.
- Comunicación entre trabajadores real: OpenCode ejecutó CLI oficial `cao agent send-message`, mensaje 14 hacia Codex; Codex respondió mediante MCP, mensaje 17 hacia OpenCode. Ambos figuran delivered. La primera respuesta (15) apuntó al terminal anterior y necesitó corrección explícita del operador; no confundirla con un fallo de enrutamiento del servicio.
- OpenCode probó primero buscar herramientas MCP sin conseguir enviar el mensaje; ese intento se detuvo y se usó la CLI oficial. No se fabricaron mensajes bajo identidad de proveedor desde el controlador.
- Perfiles, copia privada de credenciales y ajustes del contenedor temporal permitieron ejecutar la prueba. No se cambiaron runtime, configuración personal ni imagen de despliegue.

RO, expiración/renovación, reinicio con tareas pendientes y propiedad de procesos detached tras eliminar solo una sesión no se han acreditado. Los recibos volvieron a bloquear una entrada al supervisor de integración tras entregar un mensaje; no está resuelta la continuación autónoma. La consulta posterior de resultado de ese turno devolvió 500, y se conserva la evidencia privada: no declarar recibo final exitoso.

## Cierre y conservación

Las tres sesiones propias restantes se eliminaron por API (200, sin errores); listado posterior de sesiones vacío. La app temporal se detuvo y eliminó por nombre exacto. Puertos19881/19981 sin servicio. Instalación personal cao-personal confirmada running/healthy al cierre. Proyecto, JSON y captura conservados; evidencia privada fuera del repositorio. El cierre del contenedor acredita ausencia de procesos dentro de esa app, no una garantía general sobre procesos detached tras borrar sesiones.

## Veredicto

DEMO FUNCIONAL CON INTERVENCIÓN: API/frontend y comunicación bidireccional acreditados, junto con assign y handoff reales. T020 completa; el runner mantenible, las regresiones y las correcciones previstas en las demás tareas no se han implementado. No afirmar PASS global de la feature ni coordinación autónoma. Logs/configuraciones de prueba privados fuera del repositorio; solo conclusiones saneadas en este documento. No duplicar documentos en vault ni actualizar Graphify por esta entrega documental.


## Revisión vigente — 2026-10-05

El veredicto y los pendientes anteriores describen la ejecución del 2026-10-01.
La revisión actual, fuentes, regresiones, trazabilidad FR/SC y límites están en
[el informe de cierre](cierre-20261005.md). Se corrigieron autenticación CLI/MCP,
herencia Auth0, admisión optativa de proyectos, montajes RO y limpieza de procesos
detached; no se introdujeron otros propietarios de coordinación ni persistencia.

Batería conjunta: 1.296 PASS; tras el último ajuste Auth0: 445 PASS del área afectada.
Validador/MCP real por stdio: 23 PASS. Docker: imagen application candidata
reconstruida y dos contenedores privados con RO/RW, persistencia y hashes actuales
PASS. Composición: cinco reglas conservadas y cero rotas. La prueba nativa nueva
con tres proveedores sigue bloqueada por login Claude caducado; no hay PASS global.
17/26 tareas acreditadas, con cierre transversal condicionado a historias completas.


## Cierre final tras renovar login y corregir OpenCode

**PASS en el alcance local documentado**. 26/26 tareas acreditadas. La ejecución
final con Claude `7cc6a2aa`, Codex `6b0bf5a8` y OpenCode `7e95a829` pasó dos rondas,
peers, MCP externo disponible/caído, handoff, renovación del controlador,
reinicio con pending/reconcile y misma generación, fallo Go aislado, cancelación
durante deferred-init y timeout durable sin repetir. Salida 0; runtime eliminado.

Fronteras nuevas revisadas: identidad JWT firmada (fixture necesitaba sub),
contador de monitor volátil frente a generación durable (checker corregido),
y status OpenCode v2 frente a error real de backend (parser reparado y capturado).
API del demo traduce RecursionError de datos persistidos a su contrato JSON 500.
No se relajaron scopes, firma, generación ni controles de memoria legacy.

333 regresiones actuales PASS; Docker candidato reconstruido y pruebas reales
RO/RW/pidfd con diez módulos actuales PASS. Gate final: cinco reglas conservadas,
cero rotas. Graphify contrastado con fuente, AST actualizado del área afectada.
Cero procesos/contenedores propios sobrantes; datos originales y selector
conservados. Sin despliegue personal ni commits. La renovación acreditada es del
controlador, no recarga automática de tokens MCP vencidos. Informe, trazabilidad
FR/SC, intentos fallidos y evidencia: [cierre-20261005.md](cierre-20261005.md).
