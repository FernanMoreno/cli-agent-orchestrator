# Renovación local de credenciales MCP

Fecha: 2026-10-06. Estado: implementado, verificado y desplegado. Evidencia y límites en `cierre.md`.

## Objetivo
Mantener la coordinación de agentes ya abiertos cuando vence su token de arranque, dentro de una instalación CAO personal y local en la misma PC.

## US1 — Continuidad (P1)
Como propietario, quiero que los MCP abiertos usen una credencial renovada sin reiniciar la API, tmux, el proveedor ni el proceso MCP. Aceptación: token original expirado rechazado con 401; nuevos tool calls nativos funcionan, con mismos IDs y procesos MCP, al menos con Claude, Codex y OpenCode.

## US2 — Seguridad y compatibilidad (P1)
La renovación mantiene issuer, subject, audiencia, clave y scopes; la API sigue verificando JWT. Un archivo configurado ausente, inseguro o inválido no activa fallback al token anterior. Modo sin auth y modo token estático mantienen su comportamiento. Un token explícito del perfil prevalece sobre un archivo heredado. MCP externos no reciben contexto CAO adicional.

## US3 — Operación (P2)
El emisor personal publica credenciales privadas de forma atómica, renueva antes del vencimiento, reintenta fallos de publicación sin crear trabajadores ni reenviar tareas, y termina su hilo al salir. Se eliminan los reinicios de 12 horas que sólo servían para rotar el token. La recreación inicial conserva cuentas y datos.

## Requisitos
FR01: fuente renovable opt-in `CAO_AUTH_LOCAL_TOKEN_FILE`; lectura por petición sin caché de bearer. FR02: archivo regular, absoluto, privado del UID actual, no symlink, tamaño acotado, parent privado; errores cerrados sin revelar token. FR03: sólo emisor personal escribe; reutiliza `_write` y `mint_token`. FR04: TTL predeterminado 3600s, renovación con margen 300s; TTL configurable en configuración privada entre 60 y 86400s para aceptación real, margen máximo de la mitad de TTL. FR05: no endpoints de refresh ni reintentos automáticos de mutations HTTP. FR06: propagación sólo al MCP CAO; override estático explícito deshabilita archivo heredado. FR07: no rotación de OAuth de los proveedores ni inferencia offline.

## Criterios de cierre
Pruebas de lector/emisor/propagación y contratos; gate composición; revisión del diff; reconstrucción y despliegue; prueba real de caducidad de 60 segundos, tres proveedores vivos; prueba negativa de archivo y recuperación sin replay; estado y cuentas preservados; informe durable.
