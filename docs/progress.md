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

## Pendiente inmediato

- Ejecutar replay con artefactos reales del plano de medios.
- Calibrar thresholds de region/confianza con salidas DBE.
- Agregar fixtures representativos de detecciones reales.
- Evaluar si se requiere tracking temporal antes de pasar a escenarios EBE.

