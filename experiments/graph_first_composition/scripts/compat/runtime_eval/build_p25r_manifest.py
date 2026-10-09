#!/usr/bin/env python3
from pathlib import Path

_TARGET = (
    Path(__file__).resolve().parents[2]
    / "runtime_eval/build_p25r_manifest.py"
)
globals()["__file__"] = str(_TARGET)
exec(compile(_TARGET.read_text(encoding="utf-8"), str(_TARGET), "exec"), globals())
