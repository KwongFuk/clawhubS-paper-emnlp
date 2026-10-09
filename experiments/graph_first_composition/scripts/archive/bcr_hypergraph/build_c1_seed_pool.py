#!/usr/bin/env python3
from pathlib import Path

_TARGET = (
    Path(__file__).resolve().parents[3]
    / "experiments/graph_first_composition/scripts/static_filtering/build_c1_seed_pool.py"
)
globals()["__file__"] = str(_TARGET)
exec(compile(_TARGET.read_text(encoding="utf-8"), str(_TARGET), "exec"), globals())
