"""Config efectiva de la corrida activa (o de la ultima)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from eovrt_control.service.run_manager import UnknownRunError

router = APIRouter(prefix="/api")


@router.get("/config")
def get_effective_config(request: Request):
    try:
        return request.app.state.manager.effective_config()
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail="Todavia no hubo ninguna corrida") from exc
