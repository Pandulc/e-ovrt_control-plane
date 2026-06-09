# ADR-0003 - Persistencia JSONL inicial

## Decision

La persistencia inicial se resuelve con archivos JSONL append-only y un `summary.json` por corrida.

## Motivo

La etapa actual necesita trazabilidad experimental y bajo costo de cambio, no consultas complejas ni retencion operacional.

## Consecuencias

- Los artefactos son simples de inspeccionar y versionar fuera del repositorio.
- No hay transacciones, indices ni garantias propias de una base de datos.
- La migracion a una base mas robusta queda diferida hasta que existan requisitos concretos.

