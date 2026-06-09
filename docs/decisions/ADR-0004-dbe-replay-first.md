# ADR-0004 - Replay DBE como primer modo

## Decision

El primer modo implementado es replay offline sobre `detections.jsonl` producido por el plano de medios.

## Motivo

Permite validar patrones CR-01/CR-02 con evidencia ya disponible sin introducir dependencias de camaras, brokers, APIs o scheduling.

## Consecuencias

- El sistema no opera en tiempo real en esta version.
- Los contratos se preparan para evolucionar a EBE, pero la implementacion se mantiene centrada en DBE.

