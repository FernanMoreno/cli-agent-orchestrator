# T014.C2b.P2c.C2 — revisión de composición

Resultado: **PASS en el componente; activación global pendiente**. Permiso autenticado para una apertura física de un hijo propio, tras cargo C1 y commit B. No acepta todo el arranque de Bridge.

- Subsistemas: modelo puro, supervisor/socket kernel, gate físico común; vecinos revisados: financiación C1, journal B, ledger/retención, tres adaptadores SQL y bootstrap Bridge.
- Orden: reserva de datos iniciada/preparada y retenida → préstamo original → commit observable por otro backend → paquete3 → aceptación autenticada → consumo antes del driver. No SQL implícito, DDL ni privilegios nuevos.
- Autoridad: descriptor construido no instala permiso; aprobación débil y contexto vivo del emisor original. SO_PEERCRED y SCM_CREDENTIALS verifican al padre y al escritor real. Un fork escritor hereda socket, no identidad. Esquema exacto y acotado; FDs recibidos cerrados también en rechazo.
- Concurrencia: PID/Thread real/task exactos; otro worker no roba ni consume permiso original. Consumo atómico bajo mutex sin I/O. Destino/usuario/TLS/scope/path verificados antes/después del recorder; vista kernel revalidada. Fork conserva restricción e incertidumbre sin heredar grant.
- Fallos reproducidos: eliminación temprana del marcador después de importar runtime permitía downgrade; dos ventanas de publicación permitían perder modo estricto al hacer fork; OSError se propagaba sin tipo estable. Corregidos latch de importación, publicación restrictiva primero y rechazo tipado, sin ocultar cancelación/MemoryError.
- Ciclo de vida: rechazo de destino, recorder/driver fallido, cancelación y MemoryError no devuelven intento ni cargo. El fence terminal prevalece y sesiones existentes siguen utilizables. Sin rearm/retry automático.
- Observaciones: emitir paquete no demuestra aceptación ni TCP. Control real corrompe etiqueta del paquete pagado: el padre devuelve plan confirmado, el hijo sale24 sin abrir SQL, cargo conservado. La reserva completa sigue retenida hasta custodia independiente.
- Compatibilidad: b1/protocolo2 siguen identidad; modo3 explícito por lanzamiento y publisher. Bridge fuente sin cambios: publisher de identidad bajo modo estricto falla antes del SQL. SQL1–2 y resource1–7 conservados.
- Pruebas: 1.329 PASS, cero fallos/errores/omisiones en96archivos; todos los1.271casos previos y58nuevos. Se conservan17avisos; la cobertura de esta selección no es aceptación de cobertura global. Tres adaptadores reales verifican un primer backend y rechazo predriver de los tres siguientes, conservando SELECT42 en la sesión abierta. Negativos de protocolo, emisor, fork/exec, FD, copia, destino, task y cargo/commit incierto incluidos.
- Arquitectura: project-composition-check e Import Linter ejecutados:457archivos/2057dependencias/5KEPT/0BROKEN. Formato/imports6Python y tipos3fuentes PASS; bytes de ejecución y archivo candidato idénticos. Revisión Claude read3rondas sin bloqueantes, pruebas revisadas por raíz.
- Dependencia real: mismo contenedor/imagen PostgreSQL18.6; tras reinicio se restauró solo entorno desechable y custodio vivo, con nueva encarnación de servidor. Regresión completa posterior a restauración. Final0clientes/0bases/0roles de pruebas; contenedor/custodio conservados.
- Contrato externo: no nueva API HTTP/MCP/SDK o proveedor desplegado separadamente; Pact no aplica. Contrato Python/socket y persistencia se validan con procesos y PostgreSQL reales. Graphify privado parcial verificado contra fuente; Obsidian3páginas/lint0.

## Límites

Se protege la composición del runtime confiable; eliminación del marcador antes de importar runtime, mutación arbitraria de Python, drivers directos y exec con entorno limpio quedan fuera. Un wrapper exec preserva el hijo original; descendientes noexec no tienen autoridad.

Un permiso paga una apertura, no fija un techo de agentes. Root, broker persistente, financiación de todas las sesiones del arranque, inventario completo y custodio siguen pendientes. No se cierran T014, los diez pendientes originales, M1/M2/M3, SDK/UI ni aceptación global/provider. No se tocaron jobs históricos ni credenciales del operador.

Evidencia: [validación](validation-t014-child-sql-permission.json).
