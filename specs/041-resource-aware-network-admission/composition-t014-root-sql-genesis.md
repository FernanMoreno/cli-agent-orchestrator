# Composition Review Report — T014.R.G

**PASS WITH RISKS, componente controlado.** Génesis firmada de una conexión de control raíz sobre créditos metadata existentes; no aceptación global ni integración CLI productiva.

## Límites y contratos revisados

El emisor externo autorizado usa su canal propietario ya financiado. El principal de los recorridos se crea con la factory interna verificada de pruebas; no se ejecuta un login ni un recorrido JWT real del operador. La cuota/posición se comprueba contra el destinatario y la capacidad de firma permanece ligada al worker original del emisor. SQL5 conserva root/links owner-only, conjunto completo de créditos y huella, ordinal1 y cuota/UUID/fence comunes. Reutiliza el coste durable, sin otro debit ni claim del backend del issuer. Los checksums SQL1–4 y resource1–7 no cambian.

El commit confirmado precede a la capacidad opaca de firma. Readback y DTO no la recrean. Claves públicas confiables externas, certificado Ed25519 canónico acotado, kernel/binding/etiqueta/destino reales antes del TCP. El scope de control conserva PID/thread/task/conexión originales y confirma la fila en ese canal antes del yield. Error, fork, cierre, pérdida de resultado/ACK o fallo de claves no devuelven crédito ni rearman autoridad.

El supervisor conserva etapas y categorías allowlisted, sin payload ni identificadores/texto original. Las notas de traceback son opcionales; dos fallos de launch que ocultaban el error/owner al faltar add_note fueron reproducidos y corregidos sin cambiar los guards, mensajes ni cleanup. La simulación de ausencia del método no acredita toda una ejecución nativa Python3.10.

## Evidencia ejecutada

- **1528 PASS / 0 fallos / 0 errores / 0 omisiones**, 16 advertencias, 109 módulos. Conserva los 1.423 IDs previos y añade 105 distintos. No se suman selecciones previas como casos adicionales.
- Nueve fuentes y 15 archivos Python idénticos al archivo ejecutado; runtime y fuente congelados, soporte preservado y cierre real del descriptor.
- Proceso raíz real: una conexión PostgreSQL y journal propio ordinal2 en ese backend; clave/kernel erróneos bloqueados antes del driver; pérdida controlada del readback permite un TCP pero no yield. Fallo de clave posterior al commit conserva fila/créditos y rechaza replay. El issuer y subject distintos tienen test PostgreSQL.
- Formato/imports/compilación de 15; mypy en frío de 457 archivos de src sin errores, misma configuración y sin nuevas supresiones. PostgreSQL reutilizado exacto, sin clientes, bases ni roles de prueba al finalizar.
- Arquitectura: 460 archivos, 2078 dependencias, 5 contratos conservados y 0 rotos. Graphify privado parcial actualizado; doce archivos Graphify ajenos intactos. Tres subagentes GPT-6 Luna Max, revisión independiente y correcciones verificadas por el lead. Claude no revisó por cuota; no hubo reintentos.
- Contrato entre módulos/procesos del mismo producto: esquema/versiones/firma y tests reales. No nuevo servicio HTTP o consumidor desplegado por separado; Pact no aplica a este corte.

## Fallos y riesgos conservados

Se corrigieron cuota issuer/subject, comparación UUID/texto del observador y rol incorrecto de un test, además de pérdida del fallo/owner por notas no soportadas. Se conserva el recibo de un rechazo pre-subject anterior a la inyección commit_ack cuya causa concreta no se capturó; una repetición posterior aprobada no lo explica retrospectivamente.

Faltan el emisor de despliegue y caller CLI productivos, activar/reutilizar el control en actor/monitor/repositorios/muestreo, inventario completo y custodia independiente. No se acepta proveedor/modelo/HTTP/WS/red distribuida ni M1/M2/M3/SDK/UI. Fuera de este corte quedan las llamadas directas a add_note de bridge.py y bridge_supervisor.py: necesitan su propia corrección/validación de compatibilidad. Un grant de una sesión física no fija un número de agentes. Los diez gates globales permanecen abiertos.

[Validación](validation-t014-root-sql-genesis.json).
