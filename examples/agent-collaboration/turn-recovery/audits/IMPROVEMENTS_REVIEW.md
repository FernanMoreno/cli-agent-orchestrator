> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# Revisión de mejoras API y frontend

## Evidencia
- `python3 -m unittest -v test_api`: antes de los cambios, 3 pruebas OK; después, 5 pruebas OK (`Ran 5 tests ... OK`).
- `node --check frontend/app.js`: sin errores de sintaxis.
- Revisadas las fuentes actuales (`api/server.py`, `frontend/*`), `CONTRACT.md` y `UI_IMPROVEMENTS.md`.
- No se probó en navegador; la verificación en Chromium la hará el operador externamente.

## Coherencia
- **DELETE**: servidor (200 `{id, deleted:true}` / 404), CONTRACT.md, README y frontend (`DELETE api/notes/{id}`) coinciden.
- **Longitud 2000**: el servidor valida 1–2000 tras `strip()`; coincide con CONTRACT.md y las pruebas existentes.
- **Filtros/contadores**: `notasVisibles()` aplica búsqueda y filtro; los contadores se calculan sobre todas las notas (no sobre las visibles), con `aria-live="polite"`.
- **Accesibilidad**: labels asociados, `aria-label` en checkbox y eliminar, errores con `role="alert"`.
- **Errores**: sin `alert()`; fallos de POST/PATCH/DELETE/GET se muestran en la región de error.

## Cambios realizados
- `frontend/index.html`: añadido `maxlength="2000"` al input, para alinear el frontend con el límite del servidor (antes solo se veía un error genérico). Nota: `maxlength` cuenta unidades UTF-16 y el servidor puntos de código, así que el cliente es igual o más estricto; no hay contradicción.
- `test_api.py`: dos pruebas nuevas: (1) DELETE elimina solo la nota objetivo y un PATCH inválido sobre nota existente no la modifica; (2) servicio de `/`, `app.js`, `styles.css` con Content-Type correcto y 404 ante traversal/archivos inexistentes.

## Observaciones menores (no corregidas)
- Si un DELETE/PATCH tiene éxito pero la recarga posterior falla, se muestra el mensaje de error de la operación.
- La carpeta contiene documentos previos de colaboración (`COLLABORATION.md`, etc.) que no se modificaron.
