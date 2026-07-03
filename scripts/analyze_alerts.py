#!/usr/bin/env python3
"""Resume y compara alertas de una o dos corridas de replay.

Util para medir el efecto de un cambio (p. ej. antes/despues de las mejoras del
motor) sobre un `alerts.jsonl`: total de alertas, desglose por condicion,
pares unicos (condicion, sujeto), re-alertas por sujeto y frames con alerta.

Uso:
    python scripts/analyze_alerts.py runs/<run>/alerts.jsonl
    python scripts/analyze_alerts.py runs/<antes>/alerts.jsonl runs/<despues>/alerts.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AlertStats:
    total: int
    by_condition: Counter
    unique_pairs: int
    frames_with_alert: int
    realerts_by_subject: Counter  # solo sujetos con mas de una alerta


def _load_alerts(path: Path) -> list[dict]:
    alerts: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                alerts.append(json.loads(line))
    return alerts


def compute_stats(alerts: list[dict]) -> AlertStats:
    by_condition: Counter = Counter()
    by_subject: Counter = Counter()
    pairs: set[tuple[str, str]] = set()
    frames: set[int] = set()
    for alert in alerts:
        condition = str(alert.get("condition_id") or "")
        subject = str(alert.get("subject_key") or "")
        by_condition[condition] += 1
        by_subject[subject] += 1
        pairs.add((condition, subject))
        frame_index = alert.get("frame_index")
        if frame_index is not None:
            frames.add(int(frame_index))
    realerts = Counter({s: c for s, c in by_subject.items() if c > 1})
    return AlertStats(
        total=len(alerts),
        by_condition=by_condition,
        unique_pairs=len(pairs),
        frames_with_alert=len(frames),
        realerts_by_subject=realerts,
    )


def print_stats(label: str, stats: AlertStats) -> None:
    print(f"== {label} ==")
    print(f"  alertas totales        : {stats.total}")
    print(f"  pares (condicion,sujeto): {stats.unique_pairs}")
    print(f"  frames con alerta       : {stats.frames_with_alert}")
    print("  por condicion:")
    for condition, count in sorted(stats.by_condition.items()):
        print(f"    {condition or '(sin condicion)'}: {count}")
    if stats.realerts_by_subject:
        print("  sujetos con re-alertas (top 5):")
        for subject, count in stats.realerts_by_subject.most_common(5):
            print(f"    {subject}: {count}")
    else:
        print("  sujetos con re-alertas  : 0")
    print()


def print_delta(before: AlertStats, after: AlertStats) -> None:
    print("== delta (despues - antes) ==")
    print(f"  alertas totales        : {after.total - before.total:+d}")
    print(f"  pares (condicion,sujeto): {after.unique_pairs - before.unique_pairs:+d}")
    print(f"  frames con alerta       : {after.frames_with_alert - before.frames_with_alert:+d}")
    conditions = sorted(set(before.by_condition) | set(after.by_condition))
    print("  por condicion:")
    for condition in conditions:
        delta = after.by_condition.get(condition, 0) - before.by_condition.get(condition, 0)
        print(f"    {condition or '(sin condicion)'}: {delta:+d}")
    before_realert = sum(before.realerts_by_subject.values())
    after_realert = sum(after.realerts_by_subject.values())
    print(f"  re-alertas (suma)       : {after_realert - before_realert:+d}")
    print()


def main(argv: list[str]) -> int:
    if not (1 <= len(argv) <= 2):
        print(__doc__)
        return 2

    paths = [Path(arg) for arg in argv]
    for path in paths:
        if not path.exists():
            print(f"error: no existe {path}", file=sys.stderr)
            return 1

    stats = [compute_stats(_load_alerts(path)) for path in paths]
    if len(stats) == 1:
        print_stats(str(paths[0]), stats[0])
        return 0

    print_stats(f"antes  ({paths[0]})", stats[0])
    print_stats(f"despues ({paths[1]})", stats[1])
    print_delta(stats[0], stats[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
