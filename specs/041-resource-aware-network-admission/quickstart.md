# Validation guide — 041

1. Leer spec/plan/tasks y el manifiesto del intento. Verificar main, estado git, memoria y dueño/identidad exacta del PostgreSQL existente. No iniciar contenedores alternativos ni modelos.
2. Ejecutar los controles puros de política y reservas con COVERAGE_FILE y temporales privados. Esperado: adaptación por CPU/memoria/almacenamiento, desconocido/caducado denegados y reservas iniciadas retenidas tras cancelación.
3. Tras integrarM1, ejecutar la selección InboxService/API/contexto/orden/barridos/drenaje y sampler. La profundidad de cola y4workers de una fixture no se presentan como límite de producto.
4. Con código/runtime congelados y memoria>=6144MiB, ejecutar la aceptación real deM1 y conservar fuente, valores de política, actividad y cierre. Los deadlines originales no cambian.
5. M2 exige competencia real entre dos coordinadores sobre PostgreSQL, reservas atómicas, límites combinados y fallos inciertos. M3 exige incorporación real de nodos y encargo ordinario completado; no basta broker con Deployment creado.
6. Ejecutar `project-composition-check "$(cat .ai/project-name)"`, revisión, diff y verificación antes de integrar. Mantener pendientes los hitos sin ejecución; conservar la diferencia entre controles offline y aceptación real.
