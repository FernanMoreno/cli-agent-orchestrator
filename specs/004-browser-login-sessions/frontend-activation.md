# Ajuste autorizado: activación desde el front principal

2026-09-30. El usuario solicita conectar el login a la interfaz levantada y rechaza introducir la contraseña en una terminal. El runtime personal ya ha sido actualizado conservando la clave JWT, issuer/subject, puertos, Docker image y datos; se ha cancelado el asistente de terminal.

Decisión: integrar en la misma aplicación una pantalla inicial de configuración de cuenta, seguida del login existente y dashboard principal. La pantalla sólo está disponible en la instalación personal privada sin cuenta. La configuración exige el JWT firmado del operador actual, identidad/scopes exactos, admin y origen local exacto. El enlace del operador autoriza la configuración sin pedir que el usuario copie el token; no hay registro público ni recuperación web.

Plan acotado:
- backend: configuración personal opt-in del entorno, disponibilidad de setup, contrato POST /auth/setup y alta atómica bajo account.lock con binding existente/cookie y cambio de modo en el mismo proceso;
- frontend: gate de setup antes del dashboard, usuario felni, nueva contraseña/confirmación y recordar; después cookie/session, limpieza de bearer histórico y dashboard;
- aceptación: proceso/SQLite/JWKS/navegador reales, autorización/rechazos/concurrencia y transición setup → dashboard → logout → login;
- despliegue: wheel y bundle instalados, abrir enlace de configuración privado y verificar modo/identidad del servicio activo.

Las contraseñas sólo se introducen en la pantalla web del usuario. No se inventa ni registra ninguna contraseña. Este ajuste sustituye el requisito previo de alta únicamente por terminal con alta web autenticada por el operador existente.

Ajuste de navegación autorizado: el usuario confirma dos vistas «Iniciar sesión» y «Crear cuenta». La inicial es login; una pestaña y el enlace «¿No tienes cuenta? Crear cuenta» abren el alta. La vuelta al login elimina la contraseña del formulario desmontado. Una instalación ya configurada informa de su cuenta existente en esa vista, sin ofrecer nuevas altas.
