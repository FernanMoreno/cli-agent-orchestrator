# Implementation Plan: Instancias reales y red distribuida observable

**Branch**: `main` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Status**: Plan inicial; contratos detallados y ejecución pendientes. Este documento no autoriza dar la feature por implementada.

## Summary

Como operador quiero ver dónde trabaja cada equipo y seguir conexiones entre instancias sin confundir máquinas, procesos y agentes.

Refina la observabilidad de 018 sin declararla implementada ni equiparar el registro local a descubrimiento remoto ilimitado.

## Technical Context

- Estado y puntos de entrada: [evidence.md](evidence.md). Reutilizar los servicios existentes indicados; no crear un almacén paralelo de verdad en el navegador.
- Persistencia y permisos: conservar el propietario, proyecto, concesión, identidad de operación y revisión del dominio; proyección derivada reproducible, sin ampliar autoridad.
- Esquemas, formatos de cursores, migraciones e índices concretos: trabajo obligatorio T002 antes de implementar; no se dan por resueltos en este plan inicial.
- Dependencias de entrega: [029](../029-execution-graph-provenance/spec.md), [034](../034-graph-live-recovery/spec.md)

## Constitution Check

Evidencia antes de conclusiones; preservación de cambios ajenos y credenciales; secuencia Spec Kit → impacto Graphify verificado contra fuente → contratos → pruebas. No relajar límites o cobertura existentes. El uso de un millón de elementos sintéticos no acredita una red operativa de ese tamaño.

## Boundaries and ownership

- Entrada: hechos y solicitudes del dominio enumerados en spec.md, en un ámbito autorizado y una revisión identificada.
- Autoridad: el servicio de origen conserva estado, admisión y resultados; el grafo solo proyecta hechos y enlaza comandos autorizados.
- Salida: lectura o efecto tipado y trazable; cualquier incertidumbre permanece explícita.
- Concurrencia y recuperación: idempotencia de operaciones, conflicto de revisión, orden por fuente, revocación y falta de cobertura deben quedar definidos en T002.
- Vecinos: [009](../009-local-cao-coordination/spec.md), [018](../018-multi-instance-execution/spec.md), [019](../019-frontend-studio/spec.md). No reemplazar sus requisitos ni cerrar sus gates de forma transitiva.

## Delivery sequence

1. Revalidar alcance y recibos recientes; vincular cada FR a una fuente o a una capacidad nueva.
2. Diseñar contrato de identidad, permisos, revisiones, persistencia y errores; revisar compatibilidad y migración.
3. Preparar casos de aceptación con oráculo independiente antes de alterar el comportamiento.
4. Implementar el mínimo recorrido real de P1 y conectarlo al producto; mantener demostración separada.
5. Completar fallos, límites y privacidad; contrastar el grafo con registros autoritativos.
6. Ejecutar gates aplicables y revisar composición antes de publicar aceptación.

## Verification

- Todos los escenarios Given/When/Then y casos límite de spec.md; cada FR tiene caso positivo y negativo cuando aplique.
- Contratos de consumidor/proveedor, transacciones y recuperación con dependencias reales cuando los mocks oculten la frontera.
- Comparación de vistas grafo/lista/detalle y sus fuentes bajo duplicados, fallos y cambios de acceso.
- Para operaciones nativas: ejecución serial, mínimo de memoria vigente, binario y revisión fijados, login intacto, limpieza comprobada.
- Presupuestos de carga y latencia: fijar datos, máquina, consultas y medición en T002 antes de ejecutar. Conservar los mínimos existentes de 017/018/019; no se permite ajustarlos después para aprobar.
- `project-composition-check "$(cat .ai/project-name)"`, revisión independiente y diff final. La aceptación de producto queda pendiente hasta disponer de esos resultados.
