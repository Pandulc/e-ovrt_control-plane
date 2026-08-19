# Configs archivados

Configs de corrida y pattern sets legacy/rotos, archivados el 2026-07-18 (salvo indicación
de fecha propia). Ninguno es referenciado por `tests/` ni `src/` (verificado por grep antes
de mover). Se conservan como registro histórico; para reusar uno, moverlo de vuelta a
`configs/`.

## Configs de corrida
- `replay_cr01_cr02_v2.yaml` — archivado el **2026-08-19**: casi-duplicado de
  `configs/replay_dbe_cr01_cr02.yaml` (mismo input, mismo pattern set v2, mismas salidas;
  solo difieren `run.name`/`description`), cero referencias en `tests/` y `src/`.
- `replay_hf_*.yaml` (detections/field/temporal/video_intel) — **rotos**: su fuente
  `../fixtures/hf_media/latest/detections.jsonl` no existe (los `hf_media/**` están
  gitignored a propósito, no viajan con el repo).
- `verify_{rtsp,video}_g0_fresh.yaml` — probes de verificación G0 apuntando a runs efímeros
  del media-plane (`run_20260710_*`) que ya no existen.
- `fase0_dbe_gdino_bench_{persistence_probe,rerun}.yaml` — bench probes de Fase 0; el input
  original (82 unidades) ya no existe.

## Pattern sets
- `patterns/cr01_cr02_field_v1.yaml` — solo lo usaba `replay_hf_field.yaml` (archivado).
- `patterns/cr01_cr02_persistence_probe.yaml` — solo el probe de persistencia de Fase 0.
- `patterns/cr01_cr02_v2_probe.yaml` — solo los `verify_*_g0_fresh` (archivados).

## Activos (NO archivados)
`configs/`: `replay_dbe_cr01_cr02` (replay oficial sobre v2), `live_ebe_cr01_cr02` (live 1:1),
`replay_simulated_cr01_cr02_temporal` (usado por tests/reproducible).
`configs/patterns/`: `cr01_cr02_v2` (oficial), `cr01_cr02_v1` (deprecado F-DR9; 36 referencias
en 13 archivos de tests — fixture load-bearing, no tocar),
`cr01_cr02_temporal_eval` (tests + replay simulado).
