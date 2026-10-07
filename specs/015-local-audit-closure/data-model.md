# Data Model: Cierre de brechas locales de auditoría

**Fecha**: 2026-10-07\
**Spec**: [spec.md](spec.md)

Este documento describe entidades lógicas e invariantes observables. No propone migraciones ni nuevas tablas; el diseño físico se decidirá después de inspeccionar las fuentes y la persistencia actuales.

## Entidades

### Tarea coordinada local

- **Identidad**: identificador estable de tarea y generación.
- **Relación**: proyecto local, instancia de origen, instancia destinataria y principal que autorizó la operación.
- **Persistencia de propietario**: `requester_principal_id` nullable en la tarea del origen, asignado únicamente desde el principal autenticado. Un registro legado sin ese vínculo no autoriza acceso ordinario entre usuarios.
- **Estado**: aceptada, en ejecución, esperando, fallida, completada o cancelada.
- **Resultado**: recibo y salida asociada a la tarea.
- **Reserva de proyecto**: protección temporal contra escrituras incompatibles.
- **Invariantes**:
  - Estado y recibo describen el mismo estado terminal.
  - Reintentar la misma solicitud no crea tareas duplicadas silenciosamente.
  - Cancelación no declara libre el proyecto mientras una escritura siga activa.
  - La limpieza de una generación anterior no elimina una generación posterior.

### Autorización de par local

- **Identidad**: instancia CAO local y clave pública asociada.
- **Ámbito**: proyecto y acciones concedidas.
- **Estado**: pendiente, autorizada, revocada o no disponible.
- **Invariantes**:
  - Revocar impide nuevas operaciones.
  - No se comparte una credencial maestra reutilizable.
  - El resultado de consulta distingue una lista vacía de un proceso par no disponible.

### Evento confirmado

- **Identidad**: secuencia estable o identificador del evento.
- **Ámbito**: sesión, proyecto y permisos del cliente.
- **Cursor**: último evento que el cliente confirma haber procesado.
- **Invariantes**:
  - La reconexión comienza después del cursor confirmado.
  - La reanudación no pierde eventos; el cliente no presenta dos veces un mismo identificador.
  - Mensajes de origen no autorizado no resuelven solicitudes.
  - Los datos de autenticación reutilizables no forman parte de URL ni registros normales.

### Ejecución de workflow

- **Identidad**: ejecución, generación, paso y tarea activa.
- **Avance**: elegibilidad, estado actual, número de reintentos y siguiente instante permitido.
- **Configuración**: opciones aceptadas y campos de identidad administrados por CAO.
- **Evidencia**: resultado durable después de limpiar recursos en memoria.
- **Invariantes**:
  - Una tarea elegible puede avanzar en rondas sucesivas.
  - Los errores persistentes producen esperas acotadas, no ciclos ilimitados.
  - Campos desconocidos se reportan antes de ejecutar.
  - Opciones del usuario no reemplazan identidad administrada.

### Candidato de publicación

- **Identidad**: versión y huella del contenido que se propone publicar.
- **Controles**: nombre, resultado, estado (aprobado, fallido, omitido o no disponible) y contenido al que corresponden.
- **Actor**: persona o automatización que inicia, aprueba y publica.
- **Inventario**: componentes que conforman el artefacto.
- **Invariantes**:
  - Publicación manual y automática usan los mismos controles.
  - Un control exitoso de contenido distinto no valida el candidato actual.
  - Un control omitido o no disponible no cuenta como aprobado.

### Ejecución aislada de proveedor

- **Identidad**: ejecución, referencia de origen confiable y proveedor usado.
- **Credenciales**: autenticación temporal aislada del hogar normal del usuario.
- **Ciclo de vida**: preparada, activa, finalizada, fallida o cancelada; incluye resultado de limpieza.
- **Invariantes**:
  - Sin credencial aislada, la prueba se bloquea antes de arrancar.
  - Finalizar, fallar o cancelar retira credenciales y artefactos temporales.
  - La indisponibilidad del proveedor se identifica como bloqueada/no disponible.

### Evidencia de confianza de plugin

- **Identidad**: plugin y versión.
- **Origen**: productor declarado, fuente y revisión exacta.
- **Integridad**: huella del contenido y resultado de comprobación.
- **Compatibilidad y permisos**: requisitos declarados y autoridad solicitada.
- **Política local**: evidencia confiable, no verificada, incompatible, modificada, permitida o rechazada.
- **Invariantes**:
  - Una referencia fija no se confunde con identidad autenticada del publicador.
  - El operador ve la evidencia y los permisos antes de habilitar.
  - Una decisión de confianza respeta la política local vigente.

## Compatibilidad y persistencia

- T018 requiere añadir `requester_principal_id` nullable con migración SQLite aditiva e idempotente. Los lectores antiguos ignoran la columna; no se elimina ni transforma el contenido previo. Revertir código pierde la garantía nueva de autorización y requiere conservar esa limitación explícita.
- Las tareas y resultados existentes siguen siendo consultables después de la corrección.
- Si la implementación necesita almacenar un nuevo estado, cursor o evidencia, debe definir compatibilidad de lectura, escritura y recuperación antes de migrar.
- La autorización de un par y la identidad de workflow no deben reutilizar una credencial perteneciente a otro perfil.
