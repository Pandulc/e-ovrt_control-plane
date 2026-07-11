"""Validacion del `run_id` como segmento de path, y generacion de ids unicos.

`run_id` se usa para construir rutas bajo `runs_dir`. Se restringe a
alfanumericos, `_` y `-`: descarta `..`, `/` y demas antes de construir
cualquier ruta (defensa en profundidad, mismo criterio que el media-plane).

Postcondicion de `new_control_run_id`: el id devuelto siempre pasa
`is_valid_run_id`, es decir, siempre es un segmento de path seguro para
`runs_dir / run_id`. Si el `run.id`/`run.name` del payload no produce un id
valido, la funcion levanta `ValueError` en vez de devolver un id inseguro o
sanearlo en silencio.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from eovrt_control.config import ReplayConfig

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id))


def require_valid_run_id(run_id: str) -> None:
    """Levanta HTTPException 404 ("run desconocido") si el id no es un segmento seguro."""
    if not is_valid_run_id(run_id):
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}")


def new_control_run_id(config: ReplayConfig) -> str:
    """Id de corrida del servicio. Sufijo unico: el default del runtime tiene
    granularidad de segundo y dos corridas seguidas colisionarian sobre `runs_dir`.

    Postcondicion: el id devuelto siempre es un segmento de path seguro (pasa
    `is_valid_run_id`). Nunca se sanea el `run.id`/`run.name` del payload: si
    no cumple el formato esperado se levanta `ValueError` en vez de devolver
    un id distinto al pedido por el usuario.
    """
    if config.run.id:
        if not is_valid_run_id(config.run.id):
            raise ValueError(
                f"run.id invalido: {config.run.id!r}. "
                f"Debe cumplir el formato {RUN_ID_RE.pattern} (alfanumericos, '_' y '-')."
            )
        return config.run.id

    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config.run.name}_{stamp}_{uuid4().hex[:6]}"
    if not is_valid_run_id(run_id):
        raise ValueError(
            f"run.name invalido: {config.run.name!r} produce el id {run_id!r}, que no "
            f"cumple el formato {RUN_ID_RE.pattern} (alfanumericos, '_' y '-')."
        )
    return run_id
