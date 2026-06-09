# ADR-0002 - Python como tecnologia inicial

## Decision

La primera implementacion del plano de control se desarrolla en Python 3.11+.

## Motivo

Python reduce friccion con el resto del pipeline, facilita pruebas sobre artefactos DBE y permite compartir estilos de configuracion y contratos con el plano de medios.

## Consecuencias

- Se prioriza claridad y velocidad de iteracion sobre optimizacion temprana.
- Si el plano de control evoluciona a servicio de baja latencia, se reevaluara el runtime y la persistencia.

