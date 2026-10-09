#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/Users/guangfu/codex/clawhubS")
DEFAULT_LOG_ROOT = ROOT / "experiments" / "skillrugpull" / "run_logs" / "skills_research"


@dataclass(frozen=True)
class Step:
    profile: str
    name: str
    cmd: list[str]


PROFILE_DEFS: dict[str, list[Step | str]] = {
    "skills_sh_static": [
        Step(
            profile="skills_sh_static",
            name="skills.sh hypergraph pairs+triples",
            cmd=[
                "python3",
                "experiments/graph_first_composition/scripts/static_filtering/analyze_skills_sh_hypergraph.py",
                "--mode",
                "both",
            ],
        ),
    ],
    "skills_sh_llm_audit": [
        Step(
            profile="skills_sh_llm_audit",
            name="skills.sh heuristic llm annotator",
            cmd=["python3", "experiments/skillrugpull/trust_capability_mismatch/09_skillssh_llm_annotator.py"],
        ),
    ],
    "sc_static_pipeline": [
        Step(
            profile="sc_static_pipeline",
            name="sc stateful pair analysis",
            cmd=["python3", "-m", "clawhub_analyzer.cli", "sc-stateful-pair-report"],
        ),
        Step(
            profile="sc_static_pipeline",
            name="sc candidate universe",
            cmd=["python3", "-m", "clawhub_analyzer.cli", "sc-candidate-universe"],
        ),
        Step(
            profile="sc_static_pipeline",
            name="sc expansion set",
            cmd=["python3", "-m", "clawhub_analyzer.cli", "sc-sample-runtime-pool"],
        ),
        Step(
            profile="sc_static_pipeline",
            name="sc runtime manifest",
            cmd=["python3", "-m", "clawhub_analyzer.cli", "sc-runtime-manifest"],
        ),
    ],
    "sc_offline_runtime_smoke": [
        Step(
            profile="sc_offline_runtime_smoke",
            name="sc offline runtime smoke (archived)",
            cmd=[
                "python3",
                "-c",
                "raise SystemExit('archived: old run_sc_full_manifest_offline.py source is not present; use experiments/graph_first_composition/scripts/runtime_eval/ for current runtime eval')",
            ],
        ),
    ],
    "research_fast": [
        "@skills_sh_llm_audit",
        "@sc_static_pipeline",
    ],
    "research_static": [
        "@skills_sh_static",
        "@research_fast",
    ],
    "research_full_smoke": [
        "@research_static",
        "@sc_offline_runtime_smoke",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-loop driver for skills.sh / SC research pipelines.",
    )
    parser.add_argument(
        "--profiles",
        default="research_fast",
        help="Comma-separated profile names.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Number of loop iterations to run.",
    )
    parser.add_argument(
        "--interval-sec",
        type=int,
        default=900,
        help="Sleep interval between iterations when max-iterations > 1.",
    )
    parser.add_argument(
        "--log-root",
        type=Path,
        default=DEFAULT_LOG_ROOT,
        help="Root directory for run logs.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep running later steps even if one step fails.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved plan without executing it.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available profiles and exit.",
    )
    return parser.parse_args()


def list_profiles() -> int:
    print("Available profiles:")
    for name in sorted(PROFILE_DEFS):
        print(f"  {name}")
    return 0


def expand_profile(name: str, stack: tuple[str, ...] = ()) -> list[Step]:
    if name not in PROFILE_DEFS:
        raise KeyError(f"unknown profile: {name}")
    if name in stack:
        chain = " -> ".join(stack + (name,))
        raise ValueError(f"profile cycle detected: {chain}")
    expanded: list[Step] = []
    for item in PROFILE_DEFS[name]:
        if isinstance(item, str):
            if not item.startswith("@"):
                raise ValueError(f"invalid profile reference in {name}: {item}")
            expanded.extend(expand_profile(item[1:], stack + (name,)))
        else:
            expanded.append(item)
    return expanded


def timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def step_slug(step: Step) -> str:
    return step.name.lower().replace(" ", "_").replace("/", "_")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_step(step: Step, iter_dir: Path, index: int) -> dict[str, object]:
    step_dir = iter_dir / f"{index:02d}_{step.profile}__{step_slug(step)}"
    step_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    proc = subprocess.run(
        step.cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    elapsed = time.time() - started

    write_text(step_dir / "stdout.log", proc.stdout)
    write_text(step_dir / "stderr.log", proc.stderr)

    record = {
        "profile": step.profile,
        "name": step.name,
        "cmd": step.cmd,
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 3),
        "step_dir": str(step_dir),
        "started_at_utc": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_text(step_dir / "meta.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


def main() -> int:
    args = parse_args()
    if args.list_profiles:
        return list_profiles()

    requested_profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
    if not requested_profiles:
        raise SystemExit("no profiles selected")

    steps: list[Step] = []
    for name in requested_profiles:
        steps.extend(expand_profile(name))

    print("Resolved auto-loop plan:")
    for idx, step in enumerate(steps, start=1):
        print(f"  {idx}. [{step.profile}] {step.name}")
        print(f"     {shlex.join(step.cmd)}")

    if args.dry_run:
        return 0

    session_root = args.log_root / timestamp_now()
    session_root.mkdir(parents=True, exist_ok=True)
    write_text(
        session_root / "session_config.json",
        json.dumps(
            {
                "profiles": requested_profiles,
                "max_iterations": args.max_iterations,
                "interval_sec": args.interval_sec,
                "continue_on_error": args.continue_on_error,
                "steps": [
                    {
                        "profile": step.profile,
                        "name": step.name,
                        "cmd": step.cmd,
                    }
                    for step in steps
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    for iteration in range(1, args.max_iterations + 1):
        iter_dir = session_root / f"iter_{iteration:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== Iteration {iteration}/{args.max_iterations} ===")
        results: list[dict[str, object]] = []

        for idx, step in enumerate(steps, start=1):
            print(f"[{idx}/{len(steps)}] {step.profile}: {step.name}")
            result = run_step(step, iter_dir, idx)
            results.append(result)
            print(
                f"  returncode={result['returncode']} elapsed={result['elapsed_sec']}s logs={result['step_dir']}",
            )
            if result["returncode"] != 0 and not args.continue_on_error:
                write_text(iter_dir / "summary.json", json.dumps(results, ensure_ascii=False, indent=2) + "\n")
                print("Stopping on first failure.")
                return int(result["returncode"])

        write_text(iter_dir / "summary.json", json.dumps(results, ensure_ascii=False, indent=2) + "\n")
        failures = sum(1 for item in results if item["returncode"] != 0)
        print(f"Iteration {iteration} complete: {len(results) - failures} succeeded, {failures} failed")

        if iteration < args.max_iterations:
            print(f"Sleeping for {args.interval_sec}s before next iteration")
            time.sleep(args.interval_sec)

    print(f"\nAuto-loop run complete. Logs: {session_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
