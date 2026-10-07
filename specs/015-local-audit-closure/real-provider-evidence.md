# Aceptación con proveedores reales — 2026-10-07

Solicitud: ejecutar los proveedores reales después del cierre local del spec 015.
Resultado vigente: **9/9 combinaciones aprobadas** entre Codex, Claude y OpenCode.
Tras renovar el login de Claude se repitieron únicamente las dos celdas que
habían fallado: **2 passed, 0 failed, 0 skipped**, exit 0, **213.49 s**.
Las otras siete aprobaciones se conservan de la ejecución anterior; no se
afirma que las nueve celdas se hayan repetido en esta última ejecución.
El primer intento terminó con **7 passed, 2 failed** por turnos posteriores
con Claude como hijo; sus resultados permanecen en el historial.

## Configuración y resultados

| Proveedor | CLI instalada | Modelo seleccionado |
|---|---|---|
| Codex | 0.160.1 | `gpt-6.1-sol`, configurado en el perfil local |
| Claude Code | 2.1.292 | `sonnet`, configurado en el perfil local |
| OpenCode | 2.0.18 | `opencode-go/longcat-2.5-preview-free`, perfil de aceptación existente |

Los modelos se invocaron realmente: la consulta `opencode models --standalone`
no produjo un catálogo, por lo que no se cuenta como prueba de disponibilidad.
Los turnos aprobados comprobaron respuesta y recibo persistido con el proveedor
y modelo solicitados, sin sustituciones por mocks ni otros modelos.

| Padre → hijo | Resultado |
|---|---|
| Codex → Codex | PASS |
| Claude → Claude | PASS, primera ejecución |
| OpenCode → OpenCode | PASS |
| Codex → Claude | PASS, repetido después del login |
| Codex → OpenCode | PASS |
| Claude → Codex | PASS |
| Claude → OpenCode | PASS |
| OpenCode → Codex | PASS |
| OpenCode → Claude | PASS, repetido después del login |

Cada celda aprobada verificó el marcador del turno, el recibo `succeeded` y su
join, entrega persistida entre hermanos, cancelación con limpieza y
conservación de `reconcile` después de un timeout. La limpieza de la sesión
quedó validada en las nueve celdas, incluidas las dos fallidas.
La autenticación del padre no se acredita mediante una respuesta propia: el
turno de modelo que exige la prueba corresponde al hijo.

## Diagnóstico inicial de Claude — historial

El token de acceso del perfil original vencía a las **06:45:29.776 UTC**; la
comprobación a las **07:06:50.504 UTC** confirmó que estaba vencido. Durante los
cruces posteriores, la captura de las sesiones aisladas mostró las frases
`Not logged in` y `Please run /login`. Ambos turnos fallidos agotaron la espera
y retornaron HTTP 504. Estos intentos fallidos se conservan; las aprobaciones
vigentes proceden de una nueva ejecución real después de renovar el login.

En OpenCode → Claude se observó además un recibo persistido `reconcile` con
`error_kind=receipt_result_unverifiable`, antes de la limpieza. El turno sin
resultado verificable no fue promovido a éxito. La aprobación inicial de
Claude → Claude no demuestra que la autenticación siguiera siendo utilizable
en las ejecuciones posteriores. Después de que el usuario renovara el login,
las dos celdas pasaron con copias privadas del nuevo registro. Ambas verificaron
turno y recibo, mensajes persistidos, cancelación, reconciliación por timeout y
limpieza de sesión. No se cambió código para lograr estas aprobaciones.

No se observó ni se indujo agotamiento de cuota. Las comprobaciones de pausa
y continuación por cuota permanecen `skipped/not_applicable` dentro de la
evidencia de cada celda; no cuentan como escenarios aprobados.

## Aislamiento y reproducción

Se usó la copia Linux nativa validada del proyecto, Python 3.12.13 y las mismas
fuentes del workspace. Los SHA-256 del harness, su servidor y los tres
adaptadores coinciden; están en [real-provider-results.json](real-provider-results.json).
No se cambió código de producción ni las aserciones de la prueba.

La repetición usó directamente el workspace y su `.venv`, Python 3.12.13: la
copia Linux temporal anterior ya no estaba disponible. Los cinco hashes de
fuentes registrados y las versiones de los tres CLIs seguían siendo idénticos.

Se copiaron únicamente los registros de login declarados a un hogar privado
temporal, con archivos de modo 0600. Cada celda tuvo su propio servidor CAO,
SQLite, HOME y terminales; el socket tmux también estuvo aislado. Se retiraron
las variables heredadas de claves API y las rutas de configuración que podían
evitar ese aislamiento. Los hogares temporales y las copias de credenciales
se eliminaron al terminar, también en los fallos. No se publicó material de
autenticación ni salida completa de modelos.

Se ejecutó `test/e2e/test_real_provider_matrix.py` con:

```bash
python -m pytest -o addopts= -o junit_family=legacy \
  -m 'e2e and live_provider' test/e2e/test_real_provider_matrix.py \
  -vv --tb=short --junitxml=REPORT.xml --basetemp=PRIVATE/p
```

Opt-in `CAO_RUN_LIVE_PROVIDER_TESTS=1`, manifiesto explícito de los tres modelos
anteriores, `CAO_REAL_PROVIDER_E2E_STRICT=1` y
`CAO_REAL_PROVIDER_E2E_AUTH_HOME` absoluto, privado y distinto del HOME original.
La primera selección contenía las tres parejas del mismo proveedor; la segunda
contenía los seis cruces restantes. El preflight estricto comprueba la presencia
del login, no garantiza que el token siga siendo válido al invocar el modelo.

- Mismo proveedor: **3 passed**, exit 0, **291.96 s**.
- Cruces: **4 passed, 2 failed**, exit 1, **792.59 s**.
- Repetición tras login: **2 passed, 0 failed**, exit 0, **213.49 s**;
  filtro `codex->claude_code,opencode_cli->claude_code`.
- Logs privados y JUnit: `/home/felni/tmp-cao015-real-providers/`.
- Logs y JUnit de la repetición: `/home/felni/cao015-provider-retry/`.
- Evidencia publicable allowlisted: [real-provider-results.json](real-provider-results.json).

El JSON conserva los dos intentos fallidos y sus diagnósticos en campos
históricos. Su resumen vigente contiene nueve celdas aprobadas: siete previas
y dos repetidas. La repetición confirmó la eliminación de sus copias de
credenciales y hogares privados al terminar.

El resto de adaptadores no se ejecutó: no se prepararon perfiles aislados con
modelo y autenticación revisados para ellos. La instalación de un CLI no cuenta
como aprobación. Kiro respondió que no estaba autenticado; no se inició login
interactivo ni se habilitaron modelos de pago adicionales. No se hizo commit,
push ni publicación.
