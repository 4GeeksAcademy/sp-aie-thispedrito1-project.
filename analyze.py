#!/usr/bin/env python3
"""Compatibility entrypoint to run the incidents analyzer from repository root.

Usage:
    python analyze.py incidents-healthcore.csv
"""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    script_path = Path(__file__).parent / "scripts" / "analyze.py"
    runpy.run_path(str(script_path), run_name="__main__")
