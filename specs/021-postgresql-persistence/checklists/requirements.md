# Specification Quality Checklist: Persistencia PostgreSQL

**Created**: 2026-10-11
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] Objetivo y valor definidos; PostgreSQL es restricción explícita del usuario.
- [x] Historias y aceptación describen comportamiento observable.
- [x] Secciones obligatorias completas, sin placeholders.

## Requirement Completeness

- [x] Sin NEEDS CLARIFICATION: defaults y ámbito documentados.
- [x] Requisitos verificables para todos los stores y contratos.
- [x] Criterios medibles para cero soporte SQLite activo en CAOS, incluidas CLI, dependencias, paquete, fixtures y pruebas funcionales.
- [x] Casos de aceptación y edge cases definidos.
- [x] Estado desconocido y comandos retirados fallan cerrados sin mutar archivos ni base de datos.
- [x] Bundles OKF modernos de directorio/tar se conservan; el lector legacy `sqlite-dump.json` se retira.
- [x] Recibos/provenance PostgreSQL existentes, schema/DDL, ledger y checksums permanecen inmutables.
- [x] Fuentes legacy y filesystem claramente delimitados; la migración de SQLite queda fuera de alcance.
- [x] Dependencias y asumptions identificadas.

## Feature Readiness

- [x] Cuatro historias con pruebas independientes.
- [x] Requisitos de PostgreSQL backup/restore, concurrencia, instalación y recuperación.
- [x] Floors estrictos permanecen: backend >=87%, Apps >=90%; sin exclusiones ni reducción de umbral.
- [x] Diseño técnico se separa en plan/research/contracts.

## Notes

Actualizado el 2026-10-14 para reflejar la retirada explícita de todo SQLite. Esta lista comprueba la integridad de los requisitos, no la aceptación de la implementación. No se crea rama ni commit. Implementación y checks todavía pendientes.
