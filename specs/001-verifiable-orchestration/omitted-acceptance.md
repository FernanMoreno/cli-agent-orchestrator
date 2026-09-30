# Corrección de las omisiones y el fallo conocido — 2026-09-30

Las **118 omisiones y el xfail** del inventario anterior tienen una ejecución
satisfactoria verificable por nodeid en [omitted-acceptance.json](omitted-acceptance.json).
El inventario actual del candidato independiente es **13.491 passed**, sin casos
pendientes: 13.476 casos conservados y 15 regresiones nuevas. Es una composición
de resultados por entorno, no una única ejecución de pytest con dependencias
externas presentes simultáneamente. Los gates condicionales siguen siendo honestos
cuando un entorno no tiene esas dependencias.

## Correcciones y pruebas reales

- Claude: el footer citado deja de representar un menú activo cuando aparece
  un compositor o respuesta posterior. Se quitó el xfail después de reproducir
  el fallo; 12 casos nuevos cubren menús activos/descartados y buffer/viewport.
- Auditoría: se implementaron el evento `memory_stored` después de la escritura
  duradera, `cao memory logs --date` de sólo lectura y el cap diario por defecto.
  163 pruebas de contratos/parity y 281 de memoria/auditoría/backend pasaron;
  otras 57 verifican autoridad, ámbitos y escrituras parciales.
- Git: los dos transportes admitidos ahora verifican normalización; los rechazados
  conservan su control negativo. Ningún caso se eliminó del inventario.
- References: ausencia y presencia también se verifican en el espejo, sin crear
  archivos artificiales. Parser libtmux estricto: 30 passed con 0.53.1 instalado
  en un directorio aislado, sin cambiar la dependencia de la aplicación.
- Herramientas: gitleaks 8.30.1 (checksum oficial verificado), cargo local y los
  tres probes Codex ejecutados. OpenCode v1: 27 passed con descubrimiento real;
  su ayuda puede usar yargs y stderr, además del formato SUBCOMMANDS.
- Docker local: 14 passed T019/T020 y 4 passed local setup. La imagen usada se
  fija por digest en el reporte; no se registra ningún backend de producción.
- Linux gratuito: QEMU/KVM dentro de un contenedor local, guest Ubuntu fijado por
  checksum, Landlock ABI 11. 54 casos de seguridad pasaron y los dos casos de
  fixture/permisos restantes pasaron al repetirlos. T097: **8 passed** con timeout
  normal de 10 s y broker nologin. Dos casos de FS casefold, dos de dispositivos
  con root dentro del guest y el control de UID 1000 pasan en sus entornos reales.
- Workflows: dos pruebas reales con Codex/gpt-6-luna, login ChatGPT y sin API key.
  YAML recorre submit → agente → journal → result por HTTP; el ejemplo recorre
  validate/run --wait, cuatro turnos y fan-out. Se corrigió el parser del test
  para leer el JSON completo de la CLI. Tres regresiones prueban rechazo de
  billing API, aislamiento de configuración y limpieza incluso si falla startup.

## Composición y límites

La escritura del audit ocurre sólo tras la persistencia satisfactoria y no incluye
el contenido de la memoria; su entrega conserva el contrato best-effort existente.
El lector CLI conserva O_RDONLY y valida la fecha antes de componer la ruta.
La detección Claude mantiene los menús activos y las exclusiones de startup;
ninguna omisión se sustituyó por un proveedor o kernel simulado.

Arquitectura candidato y `project-composition-check caos` del checkout original:
5 contratos conservados, 0 rotos. Black/isort y diff-check pasan. Wheel reconstruida
con el código corregido, Web, cuatro MCP Apps y ELF de TUI; manifest/patch finales
se guardan en `/home/felni/ct/t085-candidate-manifest.json`.

No se hizo merge upstream, commit, push ni publicación. La aceptación del candidato
no convierte el host WSL ABI 7 en un host ABI 11. La VM aporta esa aceptación local.
El fallo `Login expired` de Claude corresponde a aquella ejecución. Tras renovar
el login el usuario, `claude auth status` confirmó sesión claude.ai Pro activa.
No se repitieron workflows Claude en la reconciliación documental; los dos
workflows recientes fueron aceptados con Codex. OpenCode v2 y otros proveedores
no se declaran aceptados. [Estado vigente](acceptance-status.md).

## Repetición sobre main actual

Las dos aceptaciones de workflows reales pasan también en el checkout original:
**2 passed en 177,74 s**, sin proveedor simulado. Las regresiones focalizadas
del checkout original: 327 passed/4 skips de kernel en WSL y 183 passed/1 skip
de descubrimiento v2; esos prerrequisitos se ejecutaron satisfactoriamente en
los entornos de la matriz anterior. El candidato conserva la aceptación
completa por inventario, separada de la integración upstream en main.
