# Revisión independiente de composición — recuperación

Revisión del padre sobre las fuentes finales y las regresiones de recuperación. El cierre global permanece sujeto a la suite y a los gates finales; no se infiere aprobación de una suite parcial.

## Frontera y causa

El inventario cerrado anterior no incluía las cinco tablas actuales de autoridad local. Las fixtures históricas creadas desde init_db actual tampoco retiraban esas tablas. Se añade el catálogo actual 39 sin modificar los catálogos 28–38 ni la identidad del schema Work. Las referencias de las cinco tablas se clasifican como requires_future_profile: cualquier fila impide captura y restauración portables. La lectura de recibos históricos continúa disponible; no se presenta un recibo verificable como restaurable por el perfil actual.

Una reproducción defensiva con un trigger nativo inerte mostró otra brecha: el inventario de tablas y el verificador de objetos con prefijo work_ no cerraban los objetos ejecutables nativos restantes. El perfil actual admite exactamente cuatro triggers: beads_binding_identity_immutable, workflow_outbox_identity_immutable, coordinator_events_no_update y coordinator_events_no_delete. Los verificadores existentes de Beads y continuación contrastan su SQL con el DDL exacto. No hay vistas nativas declaradas. Los objetos Work siguen contrastados con las migraciones originales y no se eliminan índices legítimos.

## Contratos contrastados en fuente

- La validación de inventario se ejecuta antes de las escrituras de restauración. Un objeto de bytes con digest válido no concede autoridad ni sustituye la validación de schema/referencias.
- El guard de objetos ejecutables se aplica al catálogo actual; los históricos permanecen congelados. Los verificados históricos no permiten saltar el requisito de perfil actual en restauración.
- El schema de Work, sus fences de writers/cuts, identidades y controles de publicación permanecen intactos. La validación usa SQLite real, no una descripción construida mediante mocks.
- Las regresiones de restauración recomponen los hashes/publicación de un bundle local con un objeto inerte no declarado, de modo que ejercitan el rechazo de schema y no solamente el rechazo de digest. Comprueban ausencia de destino/staging y bytes del origen sin cambios.
- Las anotaciones de manifiestos describen validaciones de forma previas. Los asserts posteriores no sustituyen las comprobaciones de payload/publicación ni normalizan entradas arbitrarias.

## Evidencia y límites

El primer cambio reprodujo la diferencia de tablas y pasó las 85 pruebas existentes y las 13 nuevas de autoridad de pares. La reproducción del trigger inerte falló con DIDNOTRAISE antes del guard. Los resultados finales de los cuatro casos de trigger/vista y la suite ampliada se incorporarán al informe types-memory-evidence.md tras terminar.

No se invocan cuentas, publicación ni sistemas externos. La revisión manual final del padre sustituye una revisión delegada que no terminó; no se cuenta esa ejecución delegada como evidencia. Arquitectura y verificación global se registran en el informe agregado.

Veredicto de fuente: sin P1/P2 abierto en las fronteras descritas; aprobación global pendiente de ejecución final.

Resultado agregado final: **1010 passed, 2 skipped, exit 0**, incluyendo los 17 casos de pares/objetos ejecutables. Mypy focalizado 15 fuentes y Black/isort 17 archivos aprobados. Revisión de composición de esta frontera: **PASS**, con gates globales registrados por el padre.
