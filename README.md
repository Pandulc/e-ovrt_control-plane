# E-OVRT Control Plane

Plano de control experimental para E-OVRT-VDP. Recibe evidencia perceptual generada por el plano de medios, evalua patrones de riesgo y registra alertas internas trazables.

## Alcance inicial

Esta primera iteracion baja deliberadamente la complejidad operacional para concentrarse en la logica:

- Entrada offline DBE desde `detections.jsonl` del plano de medios.
- Patrones CR-01 y CR-02 sobre evidencia `person`, `helmet` y `vest`.
- Persistencia append-only en JSONL, sin base de datos robusta.
- Estado temporal simple con persistencia por `timestamp_ms` y fallback por frames. El pattern set oficial `cr01_cr02_v2` opera con `granularity: scene`: la clave de estado es `(pattern_id, source_id)` (ADR-0012); la granularidad por sujeto queda disponible por config.
- CLI local para replay y generacion de artefactos de corrida.

El modulo tambien es un **servicio HTTP config-driven** (`eovrt-control serve`, FastAPI en `:8081`, ADR-0008) y consume el bus ZeroMQ del media-plane en vivo (ver "Servicio HTTP y camino live"). No incluye UI, brokers, base de datos relacional/documental, zonas, integracion con notificaciones externas ni decisiones normativas automaticas.

## Estructura: nucleo `eovrt_control` + `eovrt_labs`

El repo separa el nucleo de la logica de control de las herramientas experimentales:

- **`eovrt_control` (nucleo, `eovrt-control`)**: contratos, motor de patrones, replay, evaluacion y export de artefactos. Dependencias livianas (`pydantic`, `pyyaml`, `typer`, `rich`); no requiere torch ni OpenCV.
- **`eovrt_labs` (labs, `eovrt-labs`)**: generador de detecciones con inferencia real (gdino / yolo-ppe / yoloe) y visualizacion de alertas sobre video. Requiere el extra pesado `.[labs]` (torch, transformers, ultralytics, opencv). Depende del nucleo solo para los contratos y el export de alertas.

```bash
pip install -e .            # nucleo (control plane)
pip install -e ".[labs]"    # + generador y visualizacion (torch/opencv/transformers)
pip install -e ".[dev]"     # + pytest y ruff
```

## Flujo

```text
media detections.jsonl
        |
        v
DetectionEvent -> PatternEngine -> PatternStateChanged -> AlertEvent
        |                 |
        v                 v
 errors.jsonl       metrics.jsonl / summary.json
```

## Uso previsto (nucleo)

```bash
eovrt-control replay configs/replay_dbe_cr01_cr02.yaml
```

La corrida crea un directorio timestampeado bajo `runs/` (`runs/<run.name>_<timestampUTC>_<sufijo>/`, p. ej. `runs/replay_dbe_cr01_cr02_20260819T120000Z_ab12cd/`) con `effective_config.yaml`, `pattern_events.jsonl`, `alerts.jsonl`, `alerts.csv`, `pattern_progress.jsonl`, `metrics.jsonl`, `errors.jsonl` y `summary.json`.

Si los patrones exigen confirmacion multi-frame (`confirm_after_frames > 1` o `confirm_after_ms`) pero las detecciones de personas no traen un `detection_id` estable, el replay emite una advertencia (en consola y en `summary.warnings`): la persistencia temporal no podra confirmar condiciones. Genera detecciones con tracking o publica IDs estables desde el plano de medios.

## Servicio HTTP y camino live

- **`eovrt-control serve`** (ADR-0008): servicio FastAPI en `:8081` (`--host`/`--port`). Endpoints: `GET /healthz`, `GET /readyz`, `POST /api/runs` (dispara una corrida `replay` o `live`; config por referencia `config_path` o por payload `config`, ADR-009), `GET /api/runs`, `GET /api/runs/current` (snapshot de patrones activos), `GET|DELETE /api/runs/{id}`, `GET /api/runs/{id}/{alerts,pattern-progress,pattern-events,received-units}`, `GET /api/config`. La webconsole y el runner son clientes de este servicio; la CLI queda para el camino offline.
- **`eovrt-control live <config>`** (EBE, ADR-0007): consume el bus ZeroMQ del media-plane por SUB (`input.type: bus`, endpoint `tcp://<host>:5557`, topicos `media.detection.v1.` y `run.lifecycle.v1.`). Corrida 1:1 que cierra con `run_finished`. El SUB debe suscribirse ANTES de disparar el run del media-plane (PUB/SUB pierde lo previo a la suscripcion); los huecos de `seq` se cuentan como eventos perdidos, nunca se silencian.
- **Bus de alertas** (`alert_bus`, insumo del modulo de distribucion): publisher XPUB de `control.alert.v1` que bindea `tcp://0.0.0.0:5558`. **`alert_bus.enabled` es `false` por default** — sin habilitarlo, la distribucion lee 0 alertas aunque el motor produzca. El JSONL sigue siendo la verdad; el bus solo transporta.
- **`EOVRT_CONTROL_RUNS_DIR`**: directorio raiz de artefactos de corrida del servicio (default `runs/` relativo al CWD).
- **`eovrt-control evaluate-alerts` v2**: ademas de precision/recall, computa **SDR** y **TTFD** (con `--detections` y `--patterns`, spec 43 seccion 10) y **FAR/hora**, con matching bipartito optimo por ventana en ms a nivel episodio; las `re_alerts` no cuentan como FP (ADR-0011) y la censura por dimensionamiento del clip se reporta como `metric_censored`.

## Semantica de patrones

- **Asociacion EPP<->persona 1:1**: cada casco/chaleco valida a lo sumo a una persona (la mas cercana al centro de la region esperada). Un EPP ajeno ya no suprime la alerta de otra persona superpuesta.
- **Persistencia temporal**: una condicion se confirma tras `confirm_after_ms`/`confirm_after_frames` de evidencia sostenida y se resuelve tras `resolve_after_ms`/`resolve_after_frames` de evidencia limpia.
- **Expiracion de sujetos ausentes** (opcional): `subject_absent_timeout_ms`/`_frames` resuelve un sujeto que deja de observarse antes de limpiar la condicion.
- **Cooldown de re-alerta** (opcional): `realert_cooldown_ms`/`_frames` evita una nueva alerta por cada ciclo `resolved -> confirmed` dentro de la ventana. **La plataforma NO usa cooldown** (ADR-0011): el motor emite en cada confirmacion y las `re_alerts` no son falsos positivos; el cooldown queda como capacidad del motor sin uso.

El pattern set oficial y UNICO vigente es `configs/patterns/cr01_cr02_v2.yaml` (CR-01 high `confirm_after_ms: 4000`, CR-02 medium `confirm_after_ms: 7000`, `granularity: scene`). `cr01_cr02_v1.yaml` esta **deprecado** (hallazgo F-DR9: su timing por frames produce falsos `missed` contra `derive_clip_gt`) y se conserva solo como fixture de tests. `cr01_cr02_temporal_eval.yaml` ejercita persistencia + expiracion + cooldown en el fixture sintetico.

> ✎ **2026-08-28 — pattern sets de campaña (variable unica frente a `v2`; `docs/operacion/130`).**
> "Unico vigente" describe el set **de referencia**, no el unico en `configs/patterns/`. Las
> campañas del banco de clips usaron cuatro sets derivados de `v2` que cambian **una sola
> variable** cada uno y comparten sus umbrales (4.000/7.000 ms, histeresis 2.000/3.000):
> `cr01_cr02_v2_subject.yaml` (`granularity: subject`, campañas G1/R2/R4/R6/I2),
> `cr01_cr02_edir_v1.yaml` (evidencia **directa** — `person_without_helmet_direct` /
> `person_without_vest_direct`, campaña D1), `cr01_cr02_hyb_or_v1.yaml` (hibrido OR, campaña H1)
> y `cr01_bare_head_v1.yaml` (CR-01 por `bare_head`, campaña B1). La identidad por sujeto de G1
> se habilita ademas con `input.track_persons: true` (tracker IoU por `source_id`, ver
> `docs/architecture.md`; sin `track_id` degrada a escena con causa `no_track_id`). Ninguno de
> ellos reemplaza a `v2` como set oficial.

## Simulacion temporal CR-01/CR-02

El repo incluye un fixture sintetico que simula la salida del plano de medios (`media.detection.v1`) para probar persistencia temporal:

- `worker_a`: CR-01 persistente, persona sin casco durante 3+ frames.
- `worker_b`: condicion transitoria sin EPP durante 2 frames, no debe alertar.
- `worker_c`: CR-02 persistente, persona sin chaleco durante 3+ frames.

```bash
eovrt-control replay configs/replay_simulated_cr01_cr02_temporal.yaml
# los directorios de corrida son timestampeados: usar el que creo el replay
eovrt-control evaluate-alerts \
  runs/<name>_<timestampUTC>_<sufijo>/alerts.jsonl \
  fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json \
  --output runs/<name>_<timestampUTC>_<sufijo>/eval_temporal.json
```

Metricas: alertas esperadas/observadas/matcheadas, missed, unexpected, duplicates, precision, recall, F1 y latencia hasta alerta (frames y ms). El caso feliz da F1 = 1.0.

## Generador de detecciones (labs, sin plano de medios)

Para probar el control plane con inferencia real sin levantar el media plane:

```bash
pip install -e ".[labs]"
eovrt-labs generate-detections \
  --input path/a/imagenes_o_video \
  --output fixtures/hf_media/latest/detections.jsonl \
  --backend gdino \
  --device cuda \
  --track \
  --run-id run_20260702_001 \
  --source-id camera_01
eovrt-control replay configs/replay_dbe_cr01_cr02.yaml
```

Nota: `configs/replay_hf_detections.yaml` fue archivado (esta roto: su input gitignorado no viaja con el repo; ver `configs/_archive/README.md`). Para replay sobre el JSONL generado, apuntar el `input.path` de la config al archivo producido por el generador.

Backends disponibles: `gdino` (open-vocabulary, por defecto), `yolo-ppe` (construction-site-safety) y `yoloe`. El CLI expone solo los parametros esenciales; los ajustes finos de deteccion y tracking se pasan por un YAML opcional:

```bash
eovrt-labs generate-detections -i video.mp4 --backend gdino --track \
  --tuning configs/tuning/example.yaml
```

Ver `configs/tuning/example.yaml` para la lista completa de parametros y sus defaults.

## Visualizacion de alertas sobre video (labs)

Cada `replay` deja un `alerts.csv` normalizado (bbox, condicion, severidad, sujeto, clase ausente, rationale) que puede usarse para revisar frames:

```bash
eovrt-labs draw-alert-frames \
  --video path/al/video.mp4 \
  --alerts runs/<control_run>/alerts.csv \
  --output-dir runs/<control_run>/visual_alerts \
  --stage confirm
```

Tambien se aceptan `alerts.jsonl` y `pattern_events.jsonl`. La salida incluye imagenes anotadas, `index.csv` y `alerts_details.csv`.

## Estado

Ver [docs/progress.md](docs/progress.md).
