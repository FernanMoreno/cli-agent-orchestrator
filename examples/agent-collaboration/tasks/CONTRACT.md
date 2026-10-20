# Contrato API de tareas

Servidor de mismo origen en `127.0.0.1:8091` por defecto; respuestas y peticiones usan JSON UTF-8. Dentro de un contenedor, configura `CAO_DEMO_HOST=0.0.0.0` para permitir el acceso por el puerto publicado. Publica el puerto del host en loopback para mantener el servicio limitado a esta PC.

## Endpoints

- `GET /api/health` → `200 {"status":"ok"}`
- `GET /api/tasks` → `200 {"tasks":[{"id":1,"title":"Comprar pan","completed":false}]}`
- `POST /api/tasks` con `{"title":"Comprar pan"}` → `201 {"task":{"id":1,"title":"Comprar pan","completed":false}}`
- `PATCH /api/tasks/{id}` con `{"completed":true}` → `200 {"task":{"id":1,"title":"Comprar pan","completed":true}}`

El `id` es entero positivo generado por el servidor. El título debe contener texto tras quitar espacios externos; el servidor devuelve el título recortado. Para completar se acepta `completed: true` (no se permiten transiciones de vuelta a pendiente).

Los errores tienen forma `{"error":"mensaje"}`. Códigos: `400` JSON inválido/campos incorrectos, `404` ruta o tarea inexistente, `405` método no permitido y `500` fallo interno.

Los datos se guardan en `data/tasks.json`. El frontend consume estas rutas relativas para mantener el mismo origen.

## Codificación y límites de entrada

El cuerpo de POST/PATCH debe ser un objeto JSON codificado estrictamente en UTF-8. UTF-16, UTF-32 y secuencias UTF-8 inválidas se rechazan con HTTP 400 y el sobre `{"error":"mensaje"}`.

El cuerpo completo tiene un máximo de **16.384 bytes**, inclusive: se cuenta el JSON completo codificado, con claves, comillas, espacios y escapes. Un cuerpo vacío o superior al máximo devuelve HTTP 400 y no modifica las tareas. El título no tiene un máximo independiente en la API, salvo este límite de cuerpo. El formulario limita la entrada a 200 unidades UTF-16 mediante `maxlength=200`; es una restricción de la interfaz, no del contrato de la API.

Los títulos deben representar texto Unicode válido. Los escapes JSON con sustitutos aislados, por ejemplo `\ud800`, se rechazan con HTTP 400 antes de escribir archivos. Un par válido, por ejemplo `\ud83d\ude00` (😀), sigue siendo aceptado.
