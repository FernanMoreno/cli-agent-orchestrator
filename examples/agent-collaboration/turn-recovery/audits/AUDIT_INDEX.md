# Índice de auditorías y revisiones históricas

Este directorio preserva revisiones hechas durante la evolución de la demo externa. Sus encabezados indican que son registros históricos. El contrato vigente está en [`../CONTRACT.md`](../CONTRACT.md); el código y `test_api.py` son la fuente de comportamiento verificable.

| Documento | Alcance/historia | Lectura actual |
|---|---|---|
| `BACKEND_FINAL_AUDIT.md` | Revisión del backend en una fase anterior | Parcial: describe PATCH con `done` solamente; el código actual también permite editar `text` y ofrece borrados individuales/colectivos. |
| `FRONTEND_FINAL_AUDIT.md` | Revisión acotada a creación, checkbox y recarga | Histórica: no cubre búsqueda, filtros, edición, eliminación ni contadores añadidos después. |
| `IMPROVEMENTS_REVIEW.md` | Revisión de una iteración de mejoras | Registro de revisión, no aceptación actual. |
| `LATE_RECEIPT_API_REVIEW.md`, `LATE_RECEIPT_UI_REVIEW.md` | Revisión de recibos tardíos de esa iteración | Conservar como contexto histórico; no define el contrato HTTP actual. |
| `REPAIR_API_REVIEW.md`, `REPAIR_UI_REVIEW.md` | Revisión de reparaciones puntuales | Alcance puntual e histórico. |
| `UI_IMPROVEMENTS.md` | Registro de cambios de interfaz | Historial de funcionalidades; verificar contra `frontend/` y `test_api.py`. |
| `FINAL_COLLABORATION.md` | Resumen de colaboración y comprobaciones de aquella ejecución | Resultado histórico, no evidencia de que las pruebas se hayan ejecutado en este checkout. |

La suite reproducible se ejecuta con el comando del README de `turn-recovery/`. No se ejecutó al importar los archivos; esta consolidación ejecuta y reporta las pruebas separadamente.
