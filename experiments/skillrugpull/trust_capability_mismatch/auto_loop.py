#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).parent
LOG_ROOT = HERE / "logs" / "auto_loop"
EXP = "experiments/skillrugpull/trust_capability_mismatch"
PROJECT_ROOT = HERE.parent.parent.parent


@dataclass(frozen=True)
class Step:
    profile: str
    name: str
    cmd: list[str]


PROFILES: dict[str, list[Step | str]] = {
    "snapshot_core": [
        Step("snapshot_core", "build transitions", ["python3", f"{EXP}/01_build_transitions.py"]),
        Step("snapshot_core", "extract features", ["python3", f"{EXP}/02_extract_features.py"]),
        Step("snapshot_core", "semantic drift", ["python3", f"{EXP}/03_semantic_drift.py"]),
        Step("snapshot_core", "disclosure adequacy", ["python3", f"{EXP}/04_disclosure_adequacy.py"]),
        Step("snapshot_core", "prevalence analysis", ["python3", f"{EXP}/05_prevalence_analysis.py"]),
        Step("snapshot_core", "risk vs disclosure", ["python3", f"{EXP}/06_risk_vs_disclosure.py"]),
        Step("snapshot_core", "category breakdown", ["python3", f"{EXP}/07_category_breakdown.py"]),
        Step("snapshot_core", "build feature matrix", ["python3", f"{EXP}/08_build_feature_matrix.py"]),
        Step("snapshot_core", "train detector", ["python3", f"{EXP}/09_train_detector.py"]),
        Step("snapshot_core", "case studies", ["python3", f"{EXP}/10_case_studies.py"]),
        Step("snapshot_core", "research supplement", ["python3", f"{EXP}/11_research_supplement.py"]),
    ],
    "annotation": [
        Step("annotation", "heuristic llm annotator", ["python3", f"{EXP}/09_skillssh_llm_annotator.py"]),
        Step("annotation", "research supplement", ["python3", f"{EXP}/11_research_supplement.py"]),
    ],
    "full": [
        "@snapshot_core",
        "@annotation",
        Step("full", "annotation sampling", ["python3", f"{EXP}/annotation/sampling.py"]),
        Step("full", "annotation sheets", ["python3", f"{EXP}/annotation/generate_annotation_sheets.py"]),
    ],
}


def expand(name: str, stack: tuple[str, ...] = ()) -> list[Step]:
    if name not in PROFILES:
        raise KeyError(f"unknown profile: {name}")
    if name in stack:
        raise ValueError(f"profile cycle: {' -> '.join(stack + (name,))}")
    out: list[Step] = []
    for item in PROFILES[name]:
        if isinstance(item, str):
            out.extend(expand(item[1:], stack + (name,)))
        else:
            out.append(item)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Auto-loop runner for {EXP}.")
    parser.add_argument("--profile", default="full")
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--interval-sec", type=int, default=900)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_step(step: Step, out_dir: Path, idx: int) -> dict[str, object]:
    step_dir = out_dir / f"{idx:02d}_{step.profile}__{step.name.replace(' ', '_')}"
    step_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    proc = subprocess.run(step.cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
    meta = {
        "profile": step.profile,
        "name": step.name,
        "cmd": step.cmd,
        "returncode": proc.returncode,
        "elapsed_sec": round(time.time() - start, 3),
    }
    write(step_dir / "stdout.log", proc.stdout)
    write(step_dir / "stderr.log", proc.stderr)
    write(step_dir / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return meta


def main() -> int:
    args = parse_args()
    steps = expand(args.profile)
    if args.dry_run:
        for i, step in enumerate(steps, start=1):
            print(f"{i}. [{step.profile}] {' '.join(step.cmd)}")
        return 0

    session_dir = LOG_ROOT / ts()
    write(session_dir / "session.json", json.dumps({"profile": args.profile}, ensure_ascii=False, indent=2) + "\n")
    for iteration in range(1, args.max_iterations + 1):
        iter_dir = session_dir / f"iter_{iteration:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        for i, step in enumerate(steps, start=1):
            result = run_step(step, iter_dir, i)
            results.append(result)
            if result["returncode"] != 0 and not args.continue_on_error:
                write(iter_dir / "summary.json", json.dumps(results, ensure_ascii=False, indent=2) + "\n")
                return int(result["returncode"])
        write(iter_dir / "summary.json", json.dumps(results, ensure_ascii=False, indent=2) + "\n")
        if iteration < args.max_iterations:
            time.sleep(args.interval_sec)
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
