# Integration contracts

1. LAST sólo retorna resultado verificable del intento actual. Recovery activo + provider sin receipt restaurado es unavailable/reconcile, nunca éxito histórico.
2. Timeout/error post-send significa delivery_may_have_occurred=True. Workflow conserva handle y no consume retry creando otro worker. Excepción de extracción no deja run=running sin driver.
3. Verificador carga identidad, actualiza presupuesto local antes de reporting durable; fallo DB no reinicia contador ni next_retry_at. Deadline sella estado local aunque reporting falle. Late reobservations no envían input y nunca usan generación obsoleta.
4. Consumidores preservan state/generation/reason/actions de202/409;500 sigue fallo interno. Verify/cancel fenced no equivalen a interrupt. Scopes y ownership Work permanecen.
5. Explicit allowedTools=[] conserva vacío; rol desconocido se rechaza; matcher MCP usa catálogo concreto con globs. Enforcement visible coincide con configuración efectiva del provider instalado.
6. turn (objeto recovery007) y turn_sequence (índice causal) son contratos distintos y aditivos. Stale frames no cierran un despacho nuevo; cierre causal no hace settlement durable.
7. Cache task tiene propietario independiente del HTTP waiter; timeout no cancela build, admission/entries/deadline acotados. Owner/policy keys no se colapsan.
8. Plan create/update/approve/run conserva revisión y material exactos; cambios relevantes invalidan aprobación. Ninguna aprobación legacy se transforma implícitamente en plan-v2.
9. Cada accept-result genera una única continuation durable; follow-up usa herramientas/grants actuales y sólo input admisible. Retry incierto no se reemplaza por assign nuevo.
10. Runtime command op_id/channel epoch y exact backend identity gobiernan efectos/cleanup. Cancel antes de envío no inicia launch; operación enviada incierta se reconcilia, no se repite a ciegas.
