# Contratos de colaboración

## Superficies existentes

- Inicio: API autenticada `/sessions` y terminales de sesión, manteniendo parámetros/formatos. CLI/web son consumidores.
- `assign`: perfil/mensaje → identidad de trabajador e inicialización diferida; retorna sin esperar resultado.
- `handoff`: perfil/mensaje/timeout → resultado o fallo con referencia recuperable; no repetir al vencer.
- `send_message`: mensaje y destinatario opcional → inbox; sin destinatario usa caller; hermanos usan ID explícito.
- `/terminals/{id}/inbox/messages`: scopes y estados actuales; no nuevo endpoint público para hermanos.

## Entorno MCP

El MCP propio recibe terminal ID, `CAO_API_HOST`, `CAO_API_PORT`, `CAO_HOME_DIR`, `CAO_AUTH_JWKS_URI`, `CAO_AUTH_ISSUER`, `CAO_AUTH_AUDIENCE`, `CAO_AUTH_LOCAL_TOKEN` cuando están configuradas y el consumidor las necesita. Mantener overrides legítimos y no reenviar a MCP ajenos.

Codex usa nombres en `env_vars`, sin valores secretos en argv. Otros proveedores usan configuración privada 0600 o herencia verificada. No usar cookies de navegador como credencial de agente ni equiparar logout con revocación del bearer de máquina.

## Proyectos

Conservar `--workspace /ruta` RW; añadir `--workspace-readonly /ruta` RO. Modos contradictorios sobre misma ruta se rechazan. Manifest mantiene source/target/readonly. Admisión personal optativa por registro; CAO genérico sin política conserva compatibilidad. Canonicalizar rutas y symlinks; metadatos Git externos necesitan conexión declarada, no montaje automático.

## Demo y aceptación

Carpeta nueva `cao-collaboration-demo-<run_id>` en Escritorio. Claude coordina; Codex escribe `api/`; OpenCode `frontend/`; ambos acuerdan `CONTRACT.md` mediante mensajes. API de tareas y UI para listar/crear/completar, mismo origen, Python estándar y HTML/CSS/JS sin dependencias externas del proyecto.

Runner puede escribir perfiles/instrucciones/controlar procesos, pero no implementar API/frontend por los agentes. Registrar IDs, mensajes de ida/vuelta, resultados y verificación funcional con navegador. Servidor demo dentro de instancia de aceptación, puertos publicados solo loopback.

Resumen por escenario PASS/FAIL/PENDING y versiones, sin secretos. Conservar proyecto/README; limpiar solo procesos/despliegue propios. No commit, push ni publicación de imágenes.
