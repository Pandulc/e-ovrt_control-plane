# ADR-0011 - Sin cooldown en el motor: emite en cada confirmacion

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

La rama `mati` incorporo al motor un cooldown de re-alerta
(`realert_cooldown_ms/frames`), pensado como politica de campo. La revision del
spec 41 (2026-07-09) detecto dos defectos con esa capacidad dentro del motor:

- El cooldown de `field_v1` (5000 ms) quedaba inerte con las ventanas del
  informe: el ciclo minimo de re-alerta (resolver + re-confirmar, 6-10 s) ya
  supera el cooldown — un parametro que nunca actua.
- Bajo G0 (`granularity: scene`) el cooldown cambiaba de significado por
  accidente de la clave de estado: de "no repetir sobre el mismo trabajador" a
  "no repetir sobre la misma camara".

## Decision

1. El motor emite un `AlertEvent` en CADA transicion a `confirmed`, sin
   supresion posterior: `alerts.jsonl` es el registro fiel de la dinamica del
   patron. Lo que SI queda en el motor es la semantica de patron que absorbe el
   ruido perceptual: umbrales de evidencia, region, matching 1:1, histeresis
   confirm/resolve, expiracion de sujetos.
2. El cooldown de re-alerta y toda politica de notificacion (supresion por
   ventana, agrupacion, rate-limiting) pertenecen al modulo de distribucion
   (spec 45): es quien decide cuantas veces molestar a un consumidor con una
   condicion ya notificada, con outcome trazable (`suppressed_cooldown`).
3. `realert_cooldown_ms/frames` queda como capacidad del motor NO usada por la
   plataforma: `cr01_cr02_v2` lo deja sin configurar (None = desactivado, el
   default del motor). No se elimina codigo; `field_v1` lo conserva como perfil
   de diagnostico/labs.

## Motivo

1. Frontera limpia y defendible: deteccion != patron != alerta != notificacion.
2. Metricas mas honestas: con supresion en el motor, la tasa de re-alertas
   (señal de estabilidad de la percepcion, hallazgo del experimento Intel
   2026-06-26) quedaba oculta. Emitiendo siempre, esa señal se mide; suprimirla
   es decision del consumidor.
3. Elimina los dos defectos del contexto sin tocar contratos.

## Consecuencias

- `evaluate-alerts` evalua a nivel episodio: un episodio GT esta detectado si
  >= 1 alerta cae en su ventana de matching; las alertas adicionales del mismo
  episodio se reportan como `re_alerts` (metrica de estabilidad), NO como
  falsos positivos — no entran al denominador de precision. FP = alerta fuera
  de todo episodio y todo evento sub-umbral.
- La metrica de "no molestar dos veces" se medira en el tramo de distribucion,
  separada de la alerta interna.
- El pattern set vigente documenta la decision en su propia descripcion
  ("Sin cooldown (ADR-011): el motor emite en cada confirmacion").

## Fuentes

- `docs/decisiones/adr-011-frontera-politica-alertas.md` (repo `docs`,
  2026-07-09) — decision, fundamento (incluidos los dos defectos) y la
  consecuencia para la evaluacion (specs 41/43).
- `docs/specs/41-control-plane.md` seccion 7 — pattern set `cr01_cr02_v2` sin
  cooldown, `field_v1` como perfil de diagnostico.
- `docs/operacion/51-instrumentacion-5b-control-plane.md` (repo `docs`) —
  implementacion del pattern set v2 y del publisher; `52-evaluate-alerts-v2.md`
  — evaluador a nivel episodio con `re_alerts`.
- Archivo local: `docs/_archive/superpowers/plans/2026-07-11-evaluate-alerts-v2.md`.
- Citas en este repo: `configs/patterns/cr01_cr02_v2.yaml` (descripcion),
  `src/eovrt_control/evaluation/temporal.py:371,952,1079,1145`,
  `configs/_archive/patterns/cr01_cr02_v2_probe.yaml:7`.
