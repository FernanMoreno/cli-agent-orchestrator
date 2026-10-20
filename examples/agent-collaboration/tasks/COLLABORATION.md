> Historical provenance record from the external demo run dated 2026-10-01. It records the observed run and its limits; it is not a current test or acceptance result. Use this example's README commands to reproduce the standalone application.

# Prueba real de colaboración CAO

Fecha: 2026-10-01. Proyecto creado en el Escritorio para esta prueba; código escrito por proveedores reales, no por el controlador de aceptación.

| Proveedor | Terminal | Trabajo |
| --- | --- | --- |
| Claude | 279d68ff | Asignación y coordinación inicial por MCP |
| Codex | 7024eb6a | API Python, persistencia JSON y CONTRACT.md |
| OpenCode | 156cfb4e | Frontend HTML/CSS/JavaScript |
| Claude | 92931d68 | Integración, HTTP, documentación y handoff a Codex |
| OpenCode | 69762243 | Pregunta directa a Codex por CLI CAO y corrección del origen del frontend |

## Evidencia

Claude usó assign para los dos trabajadores y handoff a Codex para revisar el contrato y añadir servido estático de la web. Resultados de los trabajadores enviados al supervisor mediante send_message; caller conservado.

Mensaje14 de OpenCode69762243 a Codex7024eb6a y mensaje17 en sentido inverso: ambos delivered en inbox CAO. OpenCode usó CLI oficial; Codex usó MCP. El operador indicó el destinatario correcto porque la primera respuesta de Codex había usado un terminal antiguo.

HTTP: GET /, /styles.css, /app.js y /api/health =200; crear tarea =201; completar =200. Playwright creó Prueba navegador CAO 20261001, la completó y recargó. Checkbox y GET /api/tasks conservaron id4, completed:true. Captura: demo-preview.png.

## Intervenciones y límites

OpenCode necesitó un ajuste de PATH y la instalación nativa de su perfil en la instancia temporal. Hubo bloqueos por recibos de turno omitidos: la integración se hizo en una sesión nueva acotada, no mediante recuperación autónoma demostrada. Codex corrigió servido estático tras petición de Claude; OpenCode corrigió el origen absoluto tras fallo observado en navegador. No afirmar que los trabajadores no necesitaron correcciones.

La coordinación totalmente autónoma, renovación de credenciales, montajes RO y reinicio con mensajes pendientes siguen pendientes del spec006. Un último turno de integración quedó bloqueado tras recibir un mensaje; el operador consolidó este registro con evidencia observada, sin cambiar código de la demo.

Todos los agentes corrieron en tmux de una única app temporal. No hubo un contenedor por trabajador. Al terminar se eliminaron las sesiones y la app temporal; el proyecto, los datos y la captura se conservaron. No se reinició la instalación personal. El puerto19981 pertenecía a la publicación temporal; usa README.md para iniciar localmente de nuevo.
