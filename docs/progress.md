# Progreso

## 2026-06-09

- Se definio el alcance inicial del plano de control.
- Se adopto Python 3.11+ con Pydantic, Typer, PyYAML y pytest/ruff en desarrollo.
- Se decidio persistencia JSONL por corrida en lugar de base de datos robusta.
- Se priorizo replay DBE offline desde eventos del plano de medios.
- Se configuro el set inicial de patrones `cr01_cr02_v1`.
- Se documento la inferencia indirecta de ausencia de EPP a partir de evidencia positiva `person`, `helmet` y `vest`.
- Se implementaron contratos Pydantic compatibles con el plano de medios.
- Se implemento evaluador espacial de ausencia para CR-01 y CR-02.
- Se implemento motor de estados `inactive -> candidate -> confirmed -> sustained -> resolved`.
- Se implemento replay DBE con artefactos JSONL y `summary.json`.
- Se agregaron pruebas unitarias para configuracion, evaluador y motor de patrones.
- Se verifico la implementacion con `pytest` y `ruff`.

## 2026-06-24

- Se agrego un flujo de evaluacion temporal simulado para probar la mitad media-plane -> control-plane sin depender aun de inferencia real.
- Se incorporo un fixture `media.detection.v1` sintetico con 12 unidades visuales, tres sujetos y condiciones persistentes/transitorias.
- Se agrego `cr01_cr02_temporal_eval` con `confirm_after_frames=3` y `resolve_after_frames=2`.
- Se agrego `eovrt-control evaluate-alerts` para comparar `alerts.jsonl` contra ground truth temporal debil.
- Se definieron metricas de evaluacion: expected/observed/matched/missed/unexpected/duplicates, precision, recall, F1 y latencia hasta alerta.
- Se valido el caso feliz: 2 alertas esperadas, 2 observadas, 0 missed, 0 unexpected, precision/recall/F1 = 1.0.
- Se agregaron pruebas automatizadas del replay simulado y del reporte de alertas inesperadas.
- Se migro la persistencia temporal del motor a ventanas por `timestamp_ms`, manteniendo fallback por frames.
- Se explicito que el plano de control no realiza tracking; la identidad estable debe venir del plano de medios o de fixtures preparados.

## Pendiente inmediato

- Ejecutar replay con artefactos reales del plano de medios.
- Generar fixtures a partir de clips reales seleccionados para evaluacion de pipeline.
- Calibrar thresholds de region/confianza con salidas DBE.
- Extender ground truth temporal para ventanas, falsos positivos por minuto y tiempo maximo aceptable hasta alerta.
- Definir el contrato de identificador estable de sujeto emitido por el plano de medios antes de pasar a escenarios EBE.

