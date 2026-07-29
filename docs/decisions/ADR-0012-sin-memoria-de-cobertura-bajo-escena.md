# ADR-0012 - Sin memoria de cobertura bajo granularidad de escena

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

La rama `mati` incorporo la memoria de cobertura EPP (`coverage_memory_ms/
frames`): "este sujeto tuvo el EPP asociado hace menos de N ms, seguir
tratandolo como cubierto". Es, por construccion, estado POR SUJETO sostenido a
traves de frames (`engine/pattern_engine.py:46`). El spec 41 seccion 2.1 dejo
un hueco: bajo `granularity: scene` (G0) la clave de estado es
`(pattern_id, source_id)`, no hay `track_id`, y `detection_id` quedo prohibido
como identidad. No queda ninguna identidad a la cual colgar esa memoria.
`subject_absent_timeout_*` tiene la misma forma pero si admite una traduccion
natural a escena; la memoria de cobertura no.

## Decision

1. Bajo `granularity: scene` la memoria de cobertura NO se aplica. No es un
   error de configuracion (`field_v1` la trae y debe seguir cargando): el motor
   la ignora y declara la degradacion con causa
   `coverage_memory_unsupported_scene` en `degradation_causes[]`.
2. La memoria sobrevive unicamente bajo `granularity: subject` con `track_id`
   presente (G1, demostrativa) — el unico regimen con identidad de sujeto. No
   se elimina codigo (coherente con ADR-0011: capacidad del motor no usada por
   la plataforma).
3. `subject_absent_timeout_*` SI se reinterpreta a nivel escena: si no se
   observo ningun sujeto de la clase del patron durante la ventana, el episodio
   de escena resuelve — la traduccion es exacta porque "no hay sujetos" no
   requiere saber quienes eran.
4. El pattern set `cr01_cr02_v2` deja `coverage_memory_*` sin configurar.

## Motivo

1. A la escala temporal de la plataforma la memoria es redundante: fue diseñada
   para amortiguar parpadeo del detector (decenas de ms) y la histeresis de
   `resolve_after_ms` (2000/3000 ms) ya absorbe ese ruido con dos a tres
   ordenes de magnitud de margen.
2. Elevarla a nivel escena cambia su significado y produce falsos negativos: si
   el trabajador A esta cubierto y el B se saca el casco dentro de la ventana,
   la violacion de B queda suprimida — el mismo defecto de accidente-de-clave
   que ADR-0011 señalo para el cooldown.
3. Reintroducir identidad debil (asociacion IoU intra-escena) se descarta: es
   un tracker de facto, sin spec y sin GT de identidades para validarlo.
4. Coherencia con ADR-0011: la memoria queda del lado del motor (semantica de
   patron); este ADR solo fija que su regimen de aplicabilidad es G1.

## Consecuencias

- La decision se declaro APUESTA EMPIRICA FALSABLE (exigencia del usuario), con
  dos tests condicion de merge: (a) gate de regresion F1 = 1.0 en ambas
  granularidades sobre el fixture temporal con la memoria desactivada bajo
  escena; (b) test de parpadeo dedicado (EPP desaparece y reaparece dentro de
  una ventana menor a `resolve_after_ms` sin resolver ni re-alertar). Ambos
  pasaron: falsacion superada el 2026-07-10 (doc 34, confirmado por par
  discriminante). Si hubieran fallado, la opcion viva era memoria a nivel
  escena asumiendo el falso negativo de alternancia de personas (limitacion
  D2.2 ya declarada de G0).
- `_memory_covers` solo aplica bajo `subject` + `track_id`;
  `_expire_absent_subjects` quedo reinterpretado a escena.
- Ningun cambio de contrato: el campo sigue existiendo en `PatternTimingConfig`.

## Fuentes

- `docs/decisiones/adr-012-memoria-cobertura-bajo-g0.md` (repo `docs`,
  2026-07-09) — el hueco, la decision, el fundamento y el criterio de
  falsacion originales.
- `docs/specs/41-control-plane.md` secciones 2.1 y 7 — hueco de origen y
  correccion del pattern set.
- `docs/operacion/34-implementacion-g0-resultados-y-deuda.md` (repo `docs`) —
  falsacion superada (2026-07-10, gate F1 = 1.0, par discriminante).
- Archivo local: `docs/_archive/superpowers/plans/2026-07-09-g0-granularidad-escena.md`.
- Citas en este repo: `src/eovrt_control/engine/pattern_engine.py:46`,
  `configs/patterns/cr01_cr02_v2.yaml` (descripcion: "Sin memoria de cobertura
  (ADR-012): inaplicable bajo escena"),
  `configs/_archive/patterns/cr01_cr02_v2_probe.yaml:8`.
