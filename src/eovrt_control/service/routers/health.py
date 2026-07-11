"""Liveness/readiness. El control-plane no carga modelo: siempre esta listo."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    # A diferencia del media-plane no hay modelo que cargar; se conserva el
    # endpoint por simetria de plataforma (healthcheck del compose).
    return {"status": "ready"}
