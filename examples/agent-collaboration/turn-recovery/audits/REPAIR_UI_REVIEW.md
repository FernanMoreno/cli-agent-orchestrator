> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# Revisión UI — Controles y contadores

**Resultado:** OK, sin reparación. Inspeccionado 2026-10-02 en fuentes actuales.

- **Editar** (app.js:200): `entrarEdicion` activa `editandoId` y enfoca el textarea.
- **Guardar** (302): valida 1–2000; PATCH `api/notes/{id}` con `{text}`; deshabilita botones; 400/404/red conservan borrador.
- **Cancelar** (294): limpia estado y restaura foco al botón Editar.
- **Limpiar** (407): confirmación; DELETE `api/notes/completed`; deshabilitado sin completadas.
- **Contadores** (103): `GET /api/stats` con fallback local sobre `notas`.

`node --check frontend/app.js` → sin errores (Node v24.19.0).
