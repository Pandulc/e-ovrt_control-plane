# Arquitectura

## Rol del plano de control

El plano de control no ejecuta inferencia visual. Su responsabilidad es consumir evidencia normalizada del plano de medios, evaluar patrones definidos para condiciones de riesgo y producir eventos internos trazables.

En esta etapa, el plano de control funciona como un replay offline DBE. Esto permite validar logica de patrones y contratos sin introducir complejidad de infraestructura.

## Componentes

- `sources`: lectura de eventos del plano de medios desde JSONL.
- `contracts`: modelos Pydantic para eventos de entrada, cambios de estado, alertas, metricas y errores.
- `engine`: motor de patrones y evaluadores por familia de patron.
- `sinks`: escritura append-only en JSONL y resumen de corrida.
- `runtime`: orquestacion de una corrida completa.
- `cli`: interfaz local para ejecutar replay.

## Cadena conceptual

```text
condicion -> patron -> evidencia perceptual -> estado de patron -> alerta interna
```

Una deteccion no es una alerta. Una alerta se emite solo cuando el motor confirma que la evidencia satisface el patron configurado.

## Evaluacion CR-01/CR-02

El plano de medios produce evidencia positiva para `person`, `helmet` y `vest`. Por eso, el plano de control infiere ausencia de EPP de forma indirecta:

1. toma cada deteccion `person` como sujeto;
2. define una region esperada dentro de la caja de la persona;
3. busca detecciones del EPP requerido dentro de esa region;
4. si no encuentra evidencia suficiente, genera evidencia positiva del patron `person_without_*`.

Esta aproximacion es intencionalmente simple. Es apta para primeras corridas DBE, pero no reemplaza tracking, segmentacion, calibracion por escena ni validacion estadistica posterior.

## Persistencia temporal

El motor de patrones confirma y resuelve condiciones mediante ventanas basadas en `timestamp_ms` cuando la evidencia del plano de medios lo provee. Los umbrales `confirm_after_ms` y `resolve_after_ms` son el criterio preferido para video; `confirm_after_frames` y `resolve_after_frames` quedan como fallback para fixtures o artefactos historicos sin timestamp.

El plano de control no realiza tracking. Si una corrida requiere identidad estable entre frames, el plano de medios debe publicar un identificador estable de sujeto o track dentro de la evidencia normalizada.

## Persistencia de artefactos

Se usa JSONL append-only para conservar bajo el costo de cambio. Una base de datos mas robusta puede incorporarse luego cuando la logica este estabilizada y haya necesidades claras de consulta, retencion o integracion.

