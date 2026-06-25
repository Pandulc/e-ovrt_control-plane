# Simulacion CR-01/CR-02 temporal

Fixture sintetico compatible con `media.detection.v1`.

Representa una secuencia de 12 unidades visuales de una misma fuente de video:

- `worker_a`: comienza equipado, luego persiste sin casco durante 5 frames.
- `worker_b`: aparece sin EPP solo 2 frames; debe quedar como condicion transitoria sin alerta.
- `worker_c`: comienza equipado y luego persiste sin chaleco durante 5 frames.

La configuracion `configs/patterns/cr01_cr02_temporal_eval.yaml` confirma patrones despues de
3 frames consecutivos y resuelve despues de 2 frames limpios.

Uso:

```bash
eovrt-control replay configs/replay_simulated_cr01_cr02_temporal.yaml
eovrt-control evaluate-alerts \
  runs/simulated_cr01_cr02_temporal/alerts.jsonl \
  fixtures/simulated_media/cr01_cr02_temporal/ground_truth.json \
  --output runs/simulated_cr01_cr02_temporal/eval_temporal.json
```

Expectativa:

- 2 alertas esperadas.
- CR-01 para `worker_a`.
- CR-02 para `worker_c`.
- 0 alertas para `worker_b`.
