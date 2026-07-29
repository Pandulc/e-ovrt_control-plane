# ADR-0009 - Config experimental centralizada; el servicio la recibe por referencia o payload

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

El proyecto centralizo toda la configuracion experimental (manifiestos
paraguas, run configs de ambos planos, pattern sets, prompt sets, tuning) en el
repo `e-ovrt_experimental-setup`, con la webconsole como superficie de gestion
primaria y el runner CLI como camino headless. Habia que definir como recibe la
config este servicio y que parte NO se centraliza.

## Decision

Lo que aplica a este repo, de la decision del proyecto:

1. La config de corrida llega al disparar el run: por referencia (`config_path`
   a un YAML) o por payload completo — nunca ambas (regla anti-ambiguedad,
   spec 41 seccion 9). El request con ambas o ninguna se rechaza.
2. El servicio persiste su `effective_config` en los artefactos de la corrida:
   la trazabilidad "alerta -> configuracion" no cambia de mecanismo.
3. La config operacional NO se centraliza: puertos, env y paths de despliegue
   viven con el servicio (`service/settings.py`). El dueno del `runs_dir` es el
   DESPLIEGUE, no el experimento (seccion 2 de la decision del proyecto): lo
   que varia entre corridas/experimentos vive en experimental-setup; lo que
   define el despliegue vive con el servicio.

## Motivo

- Una unica fuente versionada de configs experimentales evita divergencia entre
  lo que corre la consola, el runner y una corrida manual.
- La frontera experimental/operacional impide que un experimento arrastre
  decisiones de despliegue (o al reves) por accidente.
- La regla referencia-XOR-payload elimina el caso ambiguo (config de archivo
  pisada parcialmente por payload) sin agregar semantica de merge.

## Consecuencias

- `service/run_request.py` modela `config_path` XOR `config` (payload);
  `config.py` carga payload sin archivo de respaldo.
- Webconsole y runner usan los mismos endpoints y las mismas configs
  versionadas; las campañas congeladas se corren por runner.
- Los catalogos por id que cada plano ya expone se conservan (regla previa del
  modulo experimental-setup).

## Fuentes

- `docs/decisiones/adr-009-config-centralizada-webconsole.md` (repo `docs`,
  2026-07-09, decision del usuario; amplia adr-004 y adr-008) — decision
  original, incluida la frontera experimental/operacional (seccion 2).
- `docs/specs/41-control-plane.md` secciones 5 y 9 (config de corrida, regla
  anti-ambiguedad).
- Citas en este repo: `src/eovrt_control/config.py:197,202`,
  `src/eovrt_control/service/run_request.py:15`,
  `src/eovrt_control/service/settings.py:1`,
  `src/eovrt_control/service/run_manager.py:353`.
