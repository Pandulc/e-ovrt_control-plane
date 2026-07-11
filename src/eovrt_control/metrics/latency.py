"""Helpers de latencia: percentiles deterministas y join t_capture->alert."""
from __future__ import annotations


def percentiles(values: list[float]) -> dict[str, float] | None:
    """P50/P95/P99 por interpolacion lineal. None si no hay datos.

    Determinista (sin dependencias de `statistics.quantiles`, que exige n>=2
    y tiene bordes distintos por metodo). Con un solo valor devuelve ese valor
    en los tres percentiles.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def _p(q: float) -> float:
        if n == 1:
            return ordered[0]
        pos = q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return ordered[lo] * (1.0 - frac) + ordered[hi] * frac

    return {"p50": _p(0.50), "p95": _p(0.95), "p99": _p(0.99)}


def join_capture_to_alert(alerts, capture_ns_by_unit, *, source_clock, two_node=False):
    """Une cada alerta (por first_evidence_unit_id) con la captura del media-plane
    y declara el estado de aplicabilidad de t_capture->alert (ADR-006, spec 40 §5.2.3).

    - wallclock single-host: computed (valor = alert_registered_ms - capture_ms).
    - media (DBE video): not_interpretable / dbe_media_time.
    - none (imagenes): not_applicable / non_temporal_source.
    - two_node sin sync: not_interpretable / clock_skew.
    - captura ausente para el unit_id: applicable_not_computed.
    """
    results = []
    for a in alerts:
        alert_id = a["alert_id"] if isinstance(a, dict) else a.alert_id
        unit_id = a["first_evidence_unit_id"] if isinstance(a, dict) else a.first_evidence_unit_id
        registered = a["alert_registered_ms"] if isinstance(a, dict) else a.alert_registered_ms
        row = {"alert_id": alert_id, "first_evidence_unit_id": unit_id,
               "status": None, "cause": None, "t_capture_to_alert_ms": None}
        if source_clock == "none":
            row["status"], row["cause"] = "not_applicable", "non_temporal_source"
        elif source_clock == "media":
            row["status"], row["cause"] = "not_interpretable", "dbe_media_time"
        elif two_node:
            row["status"], row["cause"] = "not_interpretable", "clock_skew"
        else:  # wallclock single-host
            cap_ns = capture_ns_by_unit.get(unit_id)
            if cap_ns is None or registered is None:
                row["status"] = "applicable_not_computed"
            else:
                row["status"] = "computed"
                row["t_capture_to_alert_ms"] = registered - (cap_ns / 1e6)
        results.append(row)
    return results
