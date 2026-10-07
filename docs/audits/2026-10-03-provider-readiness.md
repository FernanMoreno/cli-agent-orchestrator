# Instalación y aceptación de proveedores pendientes

Continuación autorizada en `main` de la auditoría del 2 de octubre. Los trece ejecutables del catálogo arrancan y devuelven versión. Esto acredita instalación; no acredita trece proveedores autenticados ni trece flujos de orquestación. El usuario confirmó que actualmente dispone de OpenCode, Codex y Claude; no se contrataron servicios ni se solicitaron credenciales nuevas.

## Correcciones comprobadas

Copilot resolvía un shim de Windows con ruta `/c/Users/.../copilot.bat`, incompatible con WSL, y devolvía 127. Se instaló el paquete Linux oficial en el prefijo npm del usuario, conservando el lanzador de VS Code. La invocación efectiva ahora resuelve el ejecutable Linux.

Con Copilot 1.0.91 se reprodujo después un HTTP 500 de creación: inicialización agotada tras 60 segundos. Había dos defectos de observación: el detector desconocía el pie `← open sidebar · Autopilot ...`; además, eliminar escapes del flujo ANSI conservaba diálogos y estados de carga que la TUI ya había borrado. Reconocer sólo el pie solucionaba la clasificación de una captura, pero no la inicialización real.

La corrección reside exclusivamente en `providers/copilot_cli.py`: reconocer ese pie, conservar `PROCESSING` ante redibujos con movimientos de cursor, y habilitar la recuperación existente desde dos capturas concordantes del viewport actual. Se mantienen límites de frecuencia, comprobación de generación, carga, spinner y preguntas visibles. No se habilita el compositor de tamaño fijo: su reproducción no coincidía con las dimensiones nativas. No se cambian recibos, permisos, APIs, almacenamiento ni política de reenvío.

El instalador oficial de Grok también publicó un alias `agent` y sustituyó el de Cursor. Se restauró `agent` hacia el mismo binario que `cursor-agent`; Grok conserva su propio `grok`. Kiro requería `unzip`, instalado en el directorio de binarios del usuario. Su documentación ahora utiliza el instalador oficial de Kiro en lugar del paquete npm de otro fabricante.

## Evidencia real

Copilot aprobó creación de perfil y terminal, seguido de dos órdenes distintas sobre el mismo demo `cao-turn-recovery-demo-20261002-f9086a25`. Primera: ejecutó `python3 -m unittest -q test_api`, leyó backend/frontend, informó 13 pruebas y propuso una mejora. Segunda: leyó README y explicó el propósito del proyecto, sin ejecutar de nuevo las pruebas. CAO devolvió la respuesta correspondiente a cada turno y secuencias 1 y 2. No hubo instrucciones correctivas del operador durante esta aceptación. Las inicializaciones diagnósticas anteriores fallaron antes de enviar tareas y se conservan como evidencia de reproducción.

El hash de las notas originales permanece `9540bfae64b96d11a5d146ca2008d733b816518fa5b8c56f5e7597941d97ca59`. Se borraron las copias temporales de autenticación y se cerró el terminal de prueba.

Hermes realizó una prueba directa de lectura real de `api/server.py` con herramientas nativas y respondió sobre `/api/stats`. Usó la autenticación GitHub existente, pasada sólo al proceso según el soporte oficial de Copilot en Hermes. No se guardó esa credencial en Hermes. Esta prueba no acredita su integración CAO ni coordinación MCP. Una primera prueba de saludo recibió una negativa del modelo; no se presenta como aceptación aprobada.

| CLI pendiente | Versión instalada | Alcance comprobado |
|---|---|---|
| Copilot | 1.0.91 | Modelo real y dos turnos mediante CAO |
| Kiro | 2.27.1 | Instalación; `whoami` exige login |
| Hermes | 0.21.5, upstream 2c239f19 | Lectura directa con modelo mediante Copilot; CAO pendiente |
| Kimi Code | 2.1.1 | Instalación; autenticación no configurada |
| Cursor | 2026.10.01-e373342 | Instalación; `status` indica sin login |
| Antigravity | 1.2.15 | Instalación; `models` exige login |
| Gemini | 0.62.0 | Instalación; petición rechazada por falta de autenticación |
| OMP | 18.4.12 | Instalación; petición informa modelos/autenticación ausentes |
| Grok | 1.0.46 | Instalación; `models` indica sin autenticación |
| MiniMax Code | 0.6.2 | Instalación; `exec` exige login |

Claude, Codex y OpenCode conservan la aceptación documentada el 2 de octubre. La prueba de versión fresca incluye los trece ejecutables; no se repitió aquí su orquestación completa.

## Revisión de composición

Superficie revisada: parser Copilot, inicialización, monitor de estado, entrada de turnos, extracción, inbox y restauración de adaptadores. El flujo ANSI sólo aporta actividad; la recuperación conserva el contrato de observación del viewport sin reenviar entrada. Una captura aislada no publica listo; la segunda debe concordar en la misma generación. Los contratos de inbox y restauración conservan sus identidades y exclusiones.

Verificación: 51 pruebas Copilot aprobadas; gate conjunto de monitor/inbox/orden inicial/restauración/contrato compartido/Copilot, 268 aprobadas con dos avisos de deprecación. Son conjuntos superpuestos y no se suman. Import Linter: cinco contratos conservados, cero rotos; `project-composition-check` aprobado. Regresiones capturan carga, procesamiento, pie nuevo, diálogo borrado y confirmación doble del viewport. Dependencias reales: CLI/modelo Copilot, tmux, FIFO, SQLite y servidor HTTP CAO aislado. Graphify actualizado sobre copia exacta de 385 archivos Python, verificada contra fuente; revisión del diff sin errores de whitespace.

**Veredicto: PASS WITH RISKS.** La reparación Copilot está demostrada. Ocho CLI requieren autenticación para pruebas con modelo, y Hermes todavía requiere aceptación de su integración CAO. No se acredita orquestación nativa completa de los trece proveedores.

Evidencias: índice (`evidence/2026-10-03-provider-readiness/evidence-index.json`; artefacto histórico local), aceptación Copilot (`evidence/2026-10-03-provider-readiness/copilot-cao-result.json`; artefacto histórico local), versiones (`evidence/2026-10-03-provider-readiness/provider-availability.json`; artefacto histórico local).

Fuentes de instalación: [GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli), [Kiro](https://kiro.dev/docs/cli/), [Cursor](https://cursor.com/docs/cli/installation), [Kimi Code](https://github.com/MoonshotAI/kimi-code), [OMP](https://github.com/can1357/oh-my-pi), [Hermes](https://github.com/NousResearch/hermes-agent), [autenticación Copilot en Hermes](https://hermes-agent.nousresearch.com/docs/integrations/providers/), [Grok](https://x.ai/build), [MiniMax](https://github.com/MiniMax-AI/minimax-code). Antigravity se instaló desde el endpoint oficial `https://antigravity.google/cli/install.sh` indicado por la documentación del proyecto; el arranque se verificó localmente.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
