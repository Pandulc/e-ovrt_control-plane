# Progreso

## 2026-08-19 — puesta al dia (commits sin documentar) + limpieza + imagen Docker

Una linea por commit no documentado desde la entrada anterior:

- `c1cbb56` — fix(eval): 3 artefactos de medicion que subestimaban la plataforma.
- `5327080` — feat(engine): evaluador `direct_evidence` + estrategias de evidencia
  por patron (spec 41 seccion 6).
- `b0ba763` — feat(engine): identidad por sujeto como capacidad de plataforma
  (`input.track_persons`, opt-in) + endurecimiento del camino live.
- `a7cc2fd` — configs: `smoke_claqueta.yaml`, config del humo anclado del doc 101.
- `b9a5e79` — chore(configs): archivado de los replay de la era piloto
  (`video16_clip10`; sus outputs en `runs/` ya no son reproducibles).
- `6f0107f` / `fdb1902` / `87a10aa` — diseño del contenedor del control-plane,
  correccion del healthcheck y descarte del 2026-08-13.

Hoy (2026-08-19):

- **Reversion del descarte del contenedor**: el despliegue de plataforma completa
  en Docker Compose reintroduce la imagen — implementada en `infra/docker/Dockerfile`
  (python:3.11-slim, solo nucleo sin `[labs]`, `EOVRT_CONTROL_RUNS_DIR=/data/runs`,
  healthcheck `/healthz`, puertos 8081/5558) + `.dockerignore`. Nota de estado
  actualizada en el spec del diseño.
- **Limpieza**: borrado `utils/draw_alert_frames.py` (wrapper roto, importaba un
  paquete inexistente; el real es `eovrt_labs.visualization.frame_drawing`);
  archivado `configs/replay_cr01_cr02_v2.yaml` (casi-duplicado de
  `replay_dbe_cr01_cr02.yaml`, cero referencias); `--ignore=tests/labs` codificado
  en `pyproject.toml` (`addopts`); `.gitignore` completado (`.superpowers/`,
  `.claude/`, `.DS_Store`, `*.log`).
- **Docs sincronizados**: README (servicio HTTP/live/bus de alertas/v2 oficial,
  ejemplos corregidos), architecture (live + servicio + componentes nuevos,
  tracking opcional, ADR-0011), contracts (`PatternProgress`, `control.alert.v1`,
  `ControlRunRequest`), y las notas viejas de este archivo.

## 2026-07-29 — puesta al dia (lo implementado desde 2026-07-02)

Resumen de `git log --since=2026-07-02` en `feature/control-service` y de los
docs de operacion del repo `docs` (33/34, 37/38, 51/52). Todo lo listado abajo
esta commiteado en la rama salvo indicacion contraria.

- **G0 — granularidad de escena** (`46c855b`, 2026-07-10, docs 33/34): clave de
  estado por escena `(pattern_id, source_id)`, aplicabilidad por temporalidad
  de fuente (ADR-0013: sobre imagenes la evaluacion de patrones es
  `not_applicable / non_temporal_source`, la corrida no se rechaza) y schema
  `clip_gt.v2`. Falsacion del ADR-0012 superada (gate F1 = 1.0 en ambas
  granularidades sin memoria de cobertura bajo escena).
- **Bus + runtime live 1:1 + servicio :8081** (`e5415df`, 2026-07-10,
  docs 37/38/51): el motor consume el bus ZeroMQ del media-plane con corrida
  1:1 (ADR-0007, cierra por `run_finished`; paridad replay<->stream verificada),
  servicio FastAPI `eovrt-control serve` (ADR-0008), instrumentacion
  `t_capture->alert` con estados de aplicabilidad (ADR-0006), pattern set
  oficial `cr01_cr02_v2` (CR-01 high 4000 ms / CR-02 medium 7000 ms, scene,
  sin cooldown ADR-0011, sin memoria de cobertura ADR-0012) y publisher de
  alertas `control.alert.v1` (apagado por default; insumo del spec 45).
- **evaluate-alerts v2** (`4a504b2` + `853f690` + `b3c6cc8`, doc 52 y doc 58):
  matching por ventana en ms a nivel episodio (`re_alerts` no son FP,
  ADR-0011), estados de aplicabilidad (ADR-0006), SDR + TTFD + fuente unica de
  umbrales (spec 43 seccion 10), y el cierre A1-A5: censura por
  dimensionamiento del clip (`metric_censored`), FAR/hora y **matching
  bipartito optimo** (salda la deuda A del doc 52 — el greedy podia deflacionar
  recall en P8). Cinco metricas: precision, recall, TTFD, SDR, FAR/hora.
- **Servicio ampliado**: progreso parcial de patrones y lookup por
  `media_run_id` (`5a85d45`), `DELETE /api/runs/{id}` (`a53e95e`), y snapshot
  de patrones activos en `/runs/current` — expuestos por HTTP en vivo y
  persistidos en la traza de la corrida (`5fcea11`; reporte suelto en
  `docs/reportes/2026-07-25-patterns-live-endpoint.md`, sin integrar).
- **Pattern set v1 deprecado**: la corrida live usa v2 (`ef001ff`, hallazgo
  F-DR9: con v1 —timing por frames— los episodios de `derive_clip_gt` nunca
  confirman y aparecen falsos `missed`). Hoy (2026-07-29; ✎ commiteado luego en
  `03ee8b0`): `cr01_cr02_v1.yaml` marcado DEPRECADO en el propio YAML (solo
  smoke/tests) y `replay_dbe_cr01_cr02.yaml` apuntado a v2.
- **ADRs materializados** (hoy, 2026-07-29): `docs/decisions/ADR-0006..0013`
  reconstruidos de las fuentes escritas del proyecto (repo `docs`:
  `decisiones/`, specs 41/42, operacion 33/34/37/38/51/52). Desde 0006 la
  numeracion local adopta la serie del proyecto, que es la que cita el codigo
  (nota de serie en ADR-0006). No se materializo un ADR-0005 local: el adr-005
  del proyecto (distribucion MQTT) pertenece al modulo de distribucion, aun no
  construido.

## Pendiente inmediato (2026-07-29)

- Revisar los ADR-0006..0013 recien materializados: son borradores
  reconstruidos a posteriori de los docs del proyecto; validar contra la
  memoria del decisor antes de darlos por definitivos.
- ~~Versionar el informe de resultados v2~~ ✎ hecho: el reporte
  `docs/reportes/2026-07-25-patterns-live-endpoint.md` ya esta trackeado.
- ~~Commitear la deprecacion de v1~~ ✎ hecho en `03ee8b0` (cambios en
  `configs/patterns/cr01_cr02_v1.yaml`, `configs/replay_dbe_cr01_cr02.yaml`,
  `tests/test_config.py`).
- Merge de `feature/control-service` a `main` — pendiente del usuario (`main`
  esta desactualizado).
- Tramo evaluacion (fuera de este repo, pero lo destraba): pasada humana del GT
  de video (CVAT) y corridas del banco.

---

*Lo que sigue es historico (hasta 2026-07-02). Su "Pendiente inmediato" quedo
superado por lo de arriba: replay con artefactos reales, fixtures de clips,
evaluacion temporal extendida y el contrato live ya existen.*

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

## 2026-07-02

- Se reorganizo el repo: nucleo `eovrt_control` (liviano, `eovrt-control`) separado de las herramientas experimentales `eovrt_labs` (`eovrt-labs`, extra `.[labs]`). El generador de detecciones y la visualizacion de alertas sobre video pasaron a labs; el export de `alerts.csv` quedo en el nucleo. El plano de control ya se instala/ejecuta sin torch ni OpenCV.
- Se podo el CLI del generador a los parametros esenciales y se movio el ajuste fino a un YAML opcional (`--tuning`, ver `configs/tuning/example.yaml`). `gdino` pasa a ser el backend por defecto (mejor deteccion que yolo-ppe en las pruebas). Se elimino el sistema de "variants"/`alerts_focus_*.csv` y la integracion de dibujado embebida en el generador.
- Se unifico la semantica de `stride`/`max_units` entre carpeta de imagenes y video, y se elimino la doble serializacion por unidad en el generador.
- Motor de patrones: asociacion EPP<->persona 1:1 por cercania, expiracion opcional de sujetos ausentes y cooldown opcional de re-alerta. Nuevo warning cuando la persistencia temporal no puede operar por falta de ids estables.
- Se agregaron pruebas de las tres mejoras del motor y de la carga de tuning; el fixture sintetico sigue dando F1 = 1.0.

## Pendiente inmediato (historico, congelado 2026-07-02 — superado, ver arriba)

- Ejecutar replay con artefactos reales del plano de medios.
- Generar fixtures a partir de clips reales seleccionados para evaluacion de pipeline.
- Calibrar thresholds de region/confianza con salidas DBE.
- Extender ground truth temporal para ventanas, falsos positivos por minuto y tiempo maximo aceptable hasta alerta.
- Definir el contrato de identificador estable de sujeto emitido por el plano de medios antes de pasar a escenarios EBE.

