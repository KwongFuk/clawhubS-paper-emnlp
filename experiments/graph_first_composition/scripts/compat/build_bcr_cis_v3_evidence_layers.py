#!/usr/bin/env python3
from pathlib import Path

_TARGET = (
    Path(__file__).resolve().parents[1]
    / "static_filtering/build_bcr_cis_v3_evidence_layers.py"
)
globals()["__file__"] = str(_TARGET)
exec(compile(_TARGET.read_text(encoding="utf-8"), str(_TARGET), "exec"), globals())
