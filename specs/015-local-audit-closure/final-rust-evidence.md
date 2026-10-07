# Validación Rust y CLI nativa final — 2026-10-07

**Todas las puertas Rust aplicables pasaron en Linux nativo**, con fuentes
actuales, sin modificar código ni políticas. Se verificaron las cláusulas Rust
de `.github/workflows/ci.yml`, el script real `scripts/assert_no_ffi.py` y la
política de `.github/workflows/cargo-deny.yml` / `tui/deny.toml`. El script no
está en `tui/scripts/`.

La ejecución utilizó `/home/felni/cao015-final/candidate`, Python 3.12.13,
`cargo 1.98.1`, `rustc 1.98.1` y host `x86_64-unknown-linux-gnu`. Los SHA-256 de
los **23 archivos Rust, manifiesto, lockfile, política y script no-FFI**
comparados coinciden entre candidato y workspace. `PATH` añadió exclusivamente
`/home/felni/.cargo/bin`; se limitó `CARGO_BUILD_JOBS=2` y se utilizó el target
privado `/home/felni/cao015-final-rust/target`.

| Puerta | Comando | Resultado fresco |
|---|---|---|
| Formato | `cargo fmt --check` desde `tui/` | Exit 0; 0.310 s. |
| Clippy | `cargo clippy --locked --all-targets -- -D warnings` desde `tui/` | Exit 0; 22.485 s. |
| Frontera no-FFI | `python scripts/assert_no_ffi.py --manifest-path tui/Cargo.toml --list` | Exit 0; **110 crates inspeccionados**, ninguno de `cpython`, `pyo3`, `pyo3-ffi`, `python3-sys`; 0.163 s. |
| Tests Rust y binario | `cargo test --locked --all-targets` desde `tui/` | Exit 0; **262 passed, 0 failed, 0 ignored, 0 filtered**, 50.886 s. |
| Base RustSec | `cargo-deny --manifest-path tui/Cargo.toml --locked fetch db` | Exit 0; 1.217 s. |
| Índice de crates | `cargo-deny --manifest-path tui/Cargo.toml --locked fetch index` | Exit 0. |
| Política completa | `cargo-deny --manifest-path tui/Cargo.toml --locked check --show-stats` | Exit 0 tanto antes como después de actualizar el índice. |

Los tests ejercitaron el binario real y los pseudo-terminales reales. Los
contratos HTTP se comprobaron contra un **servidor CAO real**, iniciado con el
fixture del proyecto, HOME/SQLite privados y puerto loopback libre, con espera
activa de `/health`. No se sustituyeron por respuestas HTTP simuladas. El
servidor se detuvo mediante `server.stop()` en `finally` al concluir.

| Target de test | Aprobados |
|---|---:|
| Unit tests de `src/main.rs` | 223 |
| `binary_exits_zero.rs` | 3 |
| `endpoint_contract.rs` | 4 |
| `hermeticity_tripwire.rs` | 11 |
| `no_backend_attach_call.rs` | 5 |
| `no_colour_literal_outside_theme.rs` | 7 |
| `pty.rs` | 9 |
| **Total actual** | **262** |

Estos recuentos corresponden a esta ejecución y sustituyen cualquier cifra de
validación anterior para el estado actual del código.

## Supply chain con la versión fijada en CI

Se provisionó `cargo-deny 0.20.2` en la carpeta privada de herramientas. La
versión coincide con el Docker action fijado en `cargo-deny.yml`. El archivo
oficial `cargo-deny-0.20.2-x86_64-unknown-linux-musl.tar.gz` se descargó del
release `0.20.2` de `EmbarkStudios/cargo-deny` y se verificó contra el digest
publicado por GitHub antes de extraerlo y ejecutarlo:

```text
SHA-256: 9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f
```

La base RustSec se actualizó en esta ejecución. Snapshot inspeccionado:
`f246cde705ecb3a6b421d6d5462c6d88f317db5f`, fecha de commit
`2026-10-07T10:23:44+02:00`. El índice de crates también se actualizó antes de la
última comprobación. Se conservó íntegramente la política del proyecto y el
lockfile; no se añadieron excepciones ni se rebajaron errores.

| Comprobación cargo-deny | Errores | Advertencias | Notas |
|---|---:|---:|---:|
| Advisories | 0 | 0 | 0 |
| Bans | 0 | 5 | 0 |
| Licenses | 0 | 0 | 95 |
| Sources | 0 | 0 | 0 |

Las cinco advertencias permitidas por `multiple-versions = "warn"` identifican
versiones duplicadas de `bitflags`, `hashbrown`, `syn`, `thiserror` y
`thiserror-impl`. No se ocultan ni se presentan como ausencia de advertencias.
Las cifras de crates del script no-FFI y las notas de licencias son métricas
separadas de sus respectivos grafos/checks.

Logs privados y comandos exactos: `/home/felni/cao015-final-rust/`, incluidos
`results.json`, `deny-results.json`, `tests.log`, `no-ffi.log` y
`deny-check-final.log`. No hubo commit, push ni cambios de comportamiento.
Esta evidencia valida Linux nativo; no acredita una ejecución nueva en macOS.
