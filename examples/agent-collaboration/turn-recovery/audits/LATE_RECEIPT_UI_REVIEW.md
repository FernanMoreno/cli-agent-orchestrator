> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# Revisión UI tardía — 2026-10-02

Auditoría READ-ONLY sobre las fuentes actuales (sin tocar implementación ni `notes.json`; md5 verificado antes y después).

## Verificado
- **Edición (PATCH)**: `guardarEdicion` envía `PATCH api/notes/{id}` solo con `{"text"}` recortado, conservando `id` y `done` según el contrato; validación en cliente de 1–2000 caracteres y gestión de 400/404/red sin perder el borrador. `alternarNota` envía `{"done":bool}`.
- **Limpieza de completadas**: `DELETE api/notes/completed` sin cuerpo; botón deshabilitado cuando no hay completadas y guarda contra doble envío; recarga lista y contadores.
- **Estadísticas**: contadores Total/Pendientes/Completadas desde `GET api/stats` validando enteros no negativos, con **fallback local** sobre las notas cargadas; nunca rompen el renderizado.
- Sintaxis JS correcta (`node --check`); todas las clases CSS usadas existen en `styles.css`; los IDs del DOM coinciden con `index.html`.
- Pruebas: `python3 -m unittest test_api -v` → **10/10 OK**, usando directorios temporales; `notes.json` real intacto.

## Hallazgos
1. `/api/stats` **no existe** en `CONTRACT.md` ni en `api/server.py` (responde 404): los contadores funcionan gracias al fallback local. Coordinado vía PEER_CONTRACT con el par API.
2. Cambio ajeno detectado: `LATE_RECEIPT_API_REVIEW.md` (12:16), correspondiente al par API concurrente; el frontend no fue tocado por terceros.

Estado: PEER_CONTRACT enviado a `4ace8d36`; PEER_ACK pendiente de un turno posterior.
