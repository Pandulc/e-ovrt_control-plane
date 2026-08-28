# ADR-0008 - Servicio HTTP minimo del control-plane

> Numeracion: sigue la serie de ADRs del proyecto (ver nota en ADR-0006).

## Contexto

El control-plane era solo CLI. Con el media-plane ya operando como servicio
config-driven y la webconsole como su cliente, el control-plane quedaba como el
unico componente no operable desde la consola, duplicando formas de ejecucion
en la demo. El runtime live (ADR-0007) ya exigia un proceso de larga vida
suscripto al bus.

## Decision

El control-plane se expone como servicio minimo (FastAPI en `:8081`,
`eovrt-control serve`, mismo patron que el media-plane) por encima del runtime:

- `POST` para disparar una corrida (replay o live/bus) desde una config
  referenciada o por payload (ADR-0009).
- `GET` de estado de la corrida activa — un run activo por vez (`RunManager`).
- `GET` de la config efectiva.

Explicitamente fuera: gestion de modelos (el plano no infiere), sesiones,
concurrencia de corridas, autenticacion, retencion — todo sigue amparado por
E-12 (prototipo experimental, sin hardening). La CLI (`replay`,
`validate-config`, `evaluate-alerts`, `live`) se conserva para el camino
offline y tests.

## Motivo

1. Simetria arquitectonica: los dos planos como servicios config-driven y la
   consola como cliente simplifica la narrativa de plataforma y la demo EBE.
2. Costo marginal bajo (~1-2 dias): el servicio es una cascara HTTP sobre el
   proceso de larga vida que el runtime live ya requeria.
3. Habilita configurar/disparar el control-plane desde la webconsole sin que la
   consola orqueste.

Alternativa descartada: runner CLI + proceso lanzado por script (contencion
original de la auditoria, doc 07 D4.1) — suficiente para la tesis, pero dejaba
al plano fuera de la consola.

## Consecuencias

- La webconsole pasa a ser cliente de ambos planos; el runner CLI orquesta por
  HTTP a los dos servicios.
- Regla de alcance registrada: si la agenda apretaba, se sacrificaba la cascara
  HTTP y quedaba el runner CLI; el runtime live no era sacrificable (EBE).
- En la practica la decision se supero sin reabrir E-12: el servicio crecio a
  11 endpoints (DELETE de runs, progreso de patrones, lookup por
  `media_run_id`, patrones activos en la corrida actual, health/ready).
  ✎ 2026-08-28: son **12 rutas** al HEAD `64cc976` (2 en `health.py`, 9 en
  `runs.py`, 1 en `config.py`); "11" vale solo si se cuenta GET+DELETE de
  `/runs/{id}` como un recurso (`docs/operacion/130`, R-08).

## Fuentes

- `docs/decisiones/adr-008-control-plane-servicio-minimo.md` (repo `docs`,
  2026-07-09, decision del usuario — excepcion registrada al doc 10) —
  decision, alternativas y fundamento originales.
- `docs/specs/41-control-plane.md` seccion 5 (servicio minimo) — contrato.
- `docs/operacion/38-servicio-minimo-control-plane.md` (repo `docs`) —
  implementacion 2026-07-10, E2E con los dos servicios, 409 verificado.
- Archivo local: `docs/_archive/superpowers/plans/2026-07-10-servicio-minimo-control-plane.md`.
- Citas en este repo: `src/eovrt_control/cli.py:132`,
  `src/eovrt_control/service/app.py:1`,
  `src/eovrt_control/service/run_manager.py:1`.
