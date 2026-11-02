# T014.C2b.P2c.C1 — revisión de composición

Resultado: **PASS en el componente; activación global pendiente**. Cargo previo del padre sobre una unidad ya prepagada, con lifecycle conservador. No concede permiso TCP al hijo.

- Subsistemas: canal nativo financiado, puerto físico, ledger local, preparación compartida, sujeto kernel y supervisor/registro previo al ACK. Revisados vecinos SQL journal, Bridge y cierre/cleanup.
- Dueño: Connection/raw/target/préstamo/scope/PID/Thread/task originales; reserva normal exacta del mismo ledger, iniciada y con retención previa. Actor padre y sujeto hijo separados.
- Presupuesto: no aumenta committed ni préstamos adicionales. Capacidad variable1/2/7/20 sin techo4. No se convierte crédito de control en datos ni se prepara recursivamente SQL bajo ese lane.
- Compartido: confirmación activa exacta sin SQL ni espera, antes del guard del owner y después del préstamo; no inversión de locks. El préstamo impide devolver el coste compartido original.
- Cierre: el fallo real de finish en reserva sin hold se reprodujo; ahora se rechaza antes del cargo. Finish ordinario entrega limpieza y conserva toda la huella, no solo la unidad del hijo. No hay API de devolución del cargo.
- Parciales: rechazo antes de insert, BaseException, commit sin ACK y ACK fallido después de commit conservan préstamo; el recibo confirmado no se degrada ni pierde cargo al envolver el error. No se concede retry.
- Contratos: protocolo2/b1 continúa como identidad; session_reservation es composición explícita opcional. Sin API HTTP/MCP/SDK nueva, Pact externo no aplica. Tests ejercen el contrato Python/socket con hijo real y PostgreSQL real.
- Migraciones: sin DDL/ACL ni cambios a SQL1–2/resource1–7. La aceptación no introduce credenciales administrativas ni conexiones implícitas.
- Pruebas: 1.271 PASS, cero fallos/errores/omisiones en93archivos; los1.237casos previos íntegros,34nuevos. Formato/imports9Python y tipos6fuentes PASS. Fuente/soporte/archivo ejecutado intactos.
- PG: mismo contenedor y dueño reutilizable; final0clientes externos/0bases/0roles de pruebas. Flujo positivo de débito→registro→ACK→TCP controlado y negativo compartido con ledger2retenido→cleanup exclusivo de fixture→1metadata.
- Revisión: Claude read en3rondas y revisión raíz de las3pruebas; Graphify privado parcial verificado contra fuente. Obsidian conserva la regla de préstamo prepagado y hold obligatorio, no copias del grafo.

## Límites

La reserva de financiación queda consumida hasta un custodio independiente: si incluye receptor/RAM/CPU/otras unidades, todo permanece retenido. Preferir una reserva dedicada de almacenamiento. No hay devolución automática incluso tras rechazo probado anterior a insert. El claim fallido no permite reintento automático.

Faltan permiso/enforcement del primer TCP del hijo, financiación raíz, broker persistente, inventario completo y custodia/retirada. No se cierran T014, los diez pendientes originales, M1/M2/M3, SDK/UI ni aceptación global. No se tocaron trabajos históricos ni credenciales del operador.

Evidencia: [validación](validation-t014-child-session-charge.json).
