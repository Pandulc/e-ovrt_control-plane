from eovrt_control.metrics.latency import join_capture_to_alert, percentiles


def test_percentiles_none_on_empty():
    assert percentiles([]) is None


def test_percentiles_single_value():
    assert percentiles([12.0]) == {"p50": 12.0, "p95": 12.0, "p99": 12.0}


def test_percentiles_linear_interpolation():
    p = percentiles([0.0, 10.0, 20.0, 30.0, 40.0])
    assert p["p50"] == 20.0
    assert abs(p["p95"] - 38.0) < 1e-9   # 0.95*(5-1)=3.8 -> 30 + 0.8*10
    assert abs(p["p99"] - 39.6) < 1e-9   # 0.99*(5-1)=3.96 -> 30 + 0.96*10


def _alert(alert_id, unit_id, alert_registered_ms):
    return {"alert_id": alert_id, "first_evidence_unit_id": unit_id,
            "alert_registered_ms": alert_registered_ms}


def test_join_computed_on_wallclock_single_host():
    alerts = [_alert("a1", "u1", 5000.0)]
    cap = {"u1": 4_000_000_000}  # 4000 ms en ns
    out = join_capture_to_alert(alerts, cap, source_clock="wallclock")
    assert out[0]["status"] == "computed" and out[0]["cause"] is None
    assert abs(out[0]["t_capture_to_alert_ms"] - 1000.0) < 1e-6


def test_join_not_interpretable_on_media():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {"u1": 1}, source_clock="media")
    assert out[0]["status"] == "not_interpretable" and out[0]["cause"] == "dbe_media_time"
    assert out[0]["t_capture_to_alert_ms"] is None


def test_join_not_applicable_on_images():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {}, source_clock="none")
    assert out[0]["status"] == "not_applicable" and out[0]["cause"] == "non_temporal_source"


def test_join_two_node_is_clock_skew():
    out = join_capture_to_alert([_alert("a1", "u1", 5000.0)], {"u1": 1},
                                source_clock="wallclock", two_node=True)
    assert out[0]["status"] == "not_interpretable" and out[0]["cause"] == "clock_skew"


def test_join_missing_capture_row_is_applicable_not_computed():
    out = join_capture_to_alert([_alert("a1", "u-missing", 5000.0)], {}, source_clock="wallclock")
    assert out[0]["status"] == "applicable_not_computed"
