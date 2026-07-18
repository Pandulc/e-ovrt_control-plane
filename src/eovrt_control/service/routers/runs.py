"""API de corridas del control-plane (spec 41 SS5)."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from eovrt_control.service.run_ids import require_valid_run_id
from eovrt_control.service.run_manager import RunBusyError, RunManager, UnknownRunError
from eovrt_control.service.run_request import ControlRunRequest

router = APIRouter(prefix="/api")


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


@router.post("/runs", status_code=201)
def create_run(body: ControlRunRequest, request: Request):
    try:
        control_run_id = _manager(request).start_run(body)
    except RunBusyError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "active_run_id": exc.active_run_id},
        )
    except (ValueError, FileNotFoundError) as exc:
        # Config invalida, ruta relativa por payload, mode que no matchea input.type.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"control_run_id": control_run_id}


@router.get("/runs")
def list_runs(request: Request, media_run_id: str | None = Query(default=None)):
    return _manager(request).list_runs(media_run_id=media_run_id)


# ANTES de /runs/{run_id}: si no, `current` se matchea como un run_id.
@router.get("/runs/current")
def get_current_run(request: Request):
    try:
        return _manager(request).current()
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail="No hay run activo") from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    require_valid_run_id(run_id)
    try:
        return _manager(request).get(run_id)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request):
    require_valid_run_id(run_id)
    manager = _manager(request)
    try:
        info = manager.get(run_id)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc
    if info["status"] == "running":
        raise HTTPException(status_code=409, detail="No se puede borrar un run activo")
    run_dir = request.app.state.settings.runs_dir / run_id
    shutil.rmtree(run_dir, ignore_errors=True)
    return Response(status_code=204)


@router.get("/runs/{run_id}/alerts")
def get_run_alerts(run_id: str, request: Request, limit: int | None = Query(default=None, ge=0)):
    """Fuente de la vista de alertas de la webconsole (spec 44). Polling; WS/SSE diferido."""
    require_valid_run_id(run_id)
    try:
        return _manager(request).alerts(run_id, limit=limit)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc


@router.get("/runs/{run_id}/pattern-progress")
def get_run_pattern_progress(
    run_id: str, request: Request, limit: int | None = Query(default=None, ge=0)
):
    """Fuente de la vista de progreso de patrones de la webconsole. Espejo de /alerts."""
    require_valid_run_id(run_id)
    try:
        return _manager(request).pattern_progress(run_id, limit=limit)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc


@router.get("/runs/{run_id}/received-units")
def get_run_received_units(
    run_id: str, request: Request, limit: int | None = Query(default=None, ge=0)
):
    """Unidades recibidas por esta corrida, para la vista correlacionada media<->control."""
    require_valid_run_id(run_id)
    try:
        return _manager(request).received_units(run_id, limit=limit)
    except UnknownRunError as exc:
        raise HTTPException(status_code=404, detail=f"Run desconocido: {run_id}") from exc
