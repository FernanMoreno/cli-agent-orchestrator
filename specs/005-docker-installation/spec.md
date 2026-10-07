# Feature Specification: Instalación Docker integrada

**Feature Branch**: `main` (workspace actual; sin cambios de historial)
**Created**: 2026-10-01
**Status**: Approved for implementation by owner
**Input**: «hazlo, además prepara una instalación automática», tras acordar backend y frontend en una imagen, persistencia y sustitución del servicio WSL.

## User Scenarios & Testing

### US1 — Aplicación integrada (P1)

El propietario abre la misma URL y utiliza login, dashboard y API desde un único contenedor de aplicación.

**Independent Test**: arrancar imagen, comprobar HTML y API autenticada; parar contenedor y comprobar que ambos dejan de responder.

1. Given Docker activo, When se instala, Then API, frontend compilado e issuer local se ejecutan dentro del contenedor.
2. Given el contenedor parado, When se consulta la URL, Then ningún servicio host mantiene la aplicación disponible.

### US2 — Instalar y actualizar automáticamente (P1)

Un comando comprueba prerrequisitos, construye la imagen y arranca la aplicación, tanto en instalaciones nuevas como existentes.

**Independent Test**: instalar dos veces sin duplicar cuenta, clave, contenedor ni configuración; simular fallo y verificar rollback.

1. Given despliegue personal existente, When se migra, Then se conserva URL, identidad, contraseña, cookies persistentes, permisos y datos.
2. Given fallo de construcción o arranque, When termina el instalador, Then informa del fallo y mantiene/restablece el despliegue anterior.
3. Given instalación nueva, When arranca, Then permite crear cuenta desde el frontend mediante bootstrap local privado sin contraseña predeterminada.

### US3 — Integraciones y recuperación (P2)

Se conservan herramientas, configuración y credenciales de proveedores mediante montajes explícitos de runtime; ningún secreto entra en la imagen.

**Independent Test**: verificar comandos de proveedores, paths del estado y acceso al daemon; recrear contenedor y comprobar conservación de estado.

### Edge Cases

- Docker ausente/inaccesible, puerto ocupado por servicio ajeno, permisos incorrectos, imagen Work inexistente o incompatible.
- Fallo después de parar el servicio host, actualización de contenedor existente, cancelación durante migración, montaje que no conserva permisos.
- Cookies locales bajo red bridge; el proxy interno debe mantener peer loopback y eliminar cabeceras de identidad forwarded.
- El instalador no usa restore(), porque esa operación revoca sesiones deliberadamente.

## Requirements

- **FR-001**: una imagen de aplicación contiene backend, frontend compilado, issuer y proxy local supervisados.
- **FR-002**: publicación exclusiva en 127.0.0.1; JWKS no se publica; se conserva Host/Origin canónico.
- **FR-003**: la instalación conserva issuer, principal, SQLite, claves de sesiones, installation ID y paths persistidos.
- **FR-004**: datos fuera de la capa efímera del contenedor mediante montajes persistentes con UID/GID del propietario.
- **FR-005**: instalación idempotente, copia SQLite consistente, rollback y comprobaciones de salud/autenticación antes de deshabilitar el servicio host.
- **FR-006**: construcción sin credenciales ni datos privados; dependencies y assets proceden de los lockfiles del proyecto.
- **FR-007**: no se publican contraseñas, tokens o claves en argumentos, logs ni estado público del instalador.
- **FR-008**: se conservan integraciones de proveedores existentes mediante runtime y montajes explícitos; fallos de compatibilidad bloquean migración antes de parar el servicio.
- **FR-009**: Work mantiene su rootfs inmutable separado y sus workers aislados bajo demanda; la imagen de aplicación no reemplaza el digest del worker.
- **FR-010**: detener el contenedor detiene backend/frontend/issuer; reiniciarlo conserva cuentas y sesiones y reanuda servicio.
- **FR-011**: documentación de instalar, actualizar, parar, arrancar y recuperar contraseña local; Docker instalado/activo es prerrequisito declarado.

## Key Entities

- Deployment root existente: deployment.json, issuer.pem, browser-auth.sqlite3 y cao/.
- Application image: backend/frontend/proxy y tooling de runtime, sin estado privado.
- Installation manifest privado: imagen, contenedor, paths, backups y estado de migración.
- Worker image: digest inmutable existente o rootfs construido por instalador.

## Success Criteria

- **SC-001**: imagen real sirve frontend y rechaza API sin autenticación.
- **SC-002**: cookies/identidad/datos sobreviven migración y recreación.
- **SC-003**: instalación repetida no duplica ni resetea recursos.
- **SC-004**: fallo de arranque probado restaura servicio anterior.
- **SC-005**: prueba real stop/start confirma que la web depende exclusivamente del contenedor.
