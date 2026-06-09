# ADR-0001 - Alcance del repositorio

## Decision

El repositorio `e-ovrt_control-plane` contiene exclusivamente la logica del plano de control: evaluacion de patrones, generacion de alertas internas, contratos y persistencia experimental.

## Motivo

Separar este plano del procesamiento visual conserva la arquitectura del proyecto: el plano de medios genera evidencia perceptual y el plano de control decide como interpretar esa evidencia bajo patrones configurables.

## Consecuencias

- No se incorporan modelos visuales en este repositorio.
- Los contratos con el plano de medios deben mantenerse explicitos.
- Las pruebas pueden enfocarse en logica deterministica de patrones.

