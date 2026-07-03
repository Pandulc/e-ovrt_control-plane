#!/usr/bin/env python3
"""Compatibility wrapper for alert-frame visualization."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eovrt_control.visualization.alert_frames import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
