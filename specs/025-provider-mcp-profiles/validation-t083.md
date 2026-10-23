# T083 — diagnóstico de líneas lógicas en la captura tmux

2026-10-23. Diagnóstico real negativo completado; no demuestra aceptación nativa completa. Fuente de producto f2c381ed; guardias de T080/T081 y presentación T082 intactas.

T082 acreditó ACK del receptor pero falló el marcador Unicode LAST del emisor. En su captura pública extraída, el cuerpo de15caracteres está partido en2líneas; coincide con el marcador solo al quitar whitespace. Eso no demuestra si el salto viene del modelo, la TUI o la captura. No se normaliza la aceptación ni se reutiliza la captura ya limpiada como una observación nueva.

El [manual de tmux](https://man.openbsd.org/tmux.1#capture-pane) documenta que -J une filas envueltas; los saltos explícitos permanecen. El cliente actual captura filas sin esa opción. Un probe contemporáneo compara normal/-J/normal del mismo pane, resuelto y revalidado por ID/geometría, con el parser real y un único recibo coincidente. Su testigo derivado del eco es solo diagnóstico, no una prueba independiente del recibo durable.

El hook se activa una vez por fase únicamente si LAST no contiene el marcador literal, su forma compacta coincide y la metadata muestra el turno esperado completado. No reemplaza last_text, no cambia los predicados y cuenta su tiempo dentro de los75s. Límite propio2s, geometría32768celdas, salida64KiB por captura y timeout restante por llamada. Solo persiste longitudes, líneas y booleans; nunca capturas adicionales, rutas, IDs o tokens. No se habilita el trace completo ni se consulta ninguna DB nativa o del operador.

Controles puros actuales:41 PASS, con parser importado de main. Incluyen positivo soft-wrap, hard LF sin rescate, pane/nonce cambiado, deadline, geometría/salida grandes, fallo de comando, Thinking ambiguo y canarios de privacidad. El self-check cerrado del runner conserva203+5+8+10+13+35controles. La comparación AST confirma perfiles y funciones de aceptación idénticos; solo cambian validación del hash del helper y hook de observación. La revisión independiente detectó herencia innecesaria de variables DB y la posible diferencia entre pane activo y primer pane. Se corrigió con el filtro de entorno DB existente y rechazo de ventanas con más de un pane, ambos cubiertos por controles con canarios. Revisión final del candidato y una ejecución real serial permanecen pendientes.


Resultado real: FAILED en121,582s por75s de fase1, receptor ACK exacto/turno completado. El probe sí se ejecutó:0,089346s, dentro de2s y del plazo original. Pane y recibo permanecieron estables; las capturas normal/-J/normal devolvieron el mismo cuerpo de15caracteres en2líneas, literal falso y compacto verdadero. No se observó soft-wrap recuperable y no se cambia el backend por esta hipótesis. El resultado negativo no distingue salto emitido de salto explícito introducido por Mini.

Mínimo de admisión7567MiB; API/tmux/PostgreSQL/estado privado limpiados y operador/fuente/binario/runner/intérprete preservados. Bytes ejecutados conservados, gate cerrado. T083 completa el diagnóstico acotado, no aceptación de v2.
