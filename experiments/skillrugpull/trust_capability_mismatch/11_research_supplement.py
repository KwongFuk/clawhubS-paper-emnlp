#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"

TRANSITIONS_PATH = DATA_DIR / "transitions.csv"
SNAPSHOTS_PATH = DATA_DIR / "skill_snapshots.csv"
FEATURE_MATRIX_PATH = DATA_DIR / "feature_matrix.csv"
ANNOTATIONS_PATH = DATA_DIR / "skillssh_annotations.csv"
REPORT_PATH = RESULTS_DIR / "research_supplement_status.md"


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def load_feature_label_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not path.exists():
        return counts
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            label = str(row.get("label", ""))
            counts[label] += 1
    return counts


def load_annotation_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not path.exists():
        return counts
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            counts[str(row.get("final_label", ""))] += 1
    return counts


def main() -> None:
    transitions = count_csv_rows(TRANSITIONS_PATH)
    snapshots = count_csv_rows(SNAPSHOTS_PATH)
    feature_rows = count_csv_rows(FEATURE_MATRIX_PATH)
    label_counts = load_feature_label_counts(FEATURE_MATRIX_PATH)
    ann_counts = load_annotation_counts(ANNOTATIONS_PATH)
    ann_total = sum(ann_counts.values())

    lines = [
        "# SkillRugPull Research Supplement Status",
        "",
        "## Current Dataset Status",
        "",
        f"- Snapshot rows: **{snapshots:,}**",
        f"- Version transitions: **{transitions:,}**",
        f"- Feature-matrix rows: **{feature_rows:,}**",
        "",
        "## What The Current Pipeline Actually Measures",
        "",
    ]

    if transitions == 0:
        lines.extend(
            [
                "- The current `skills.sh` study is **snapshot-only**, not a real version-drift measurement.",
                "- `transitions.csv` contains only the header, so RQ1-RQ4 are currently answered through a cross-sectional proxy: current capability/disclosure mismatch.",
                "- Any wording about `trust drift` on `skills.sh` should therefore be framed as **SkillRugPull proxy candidates**, not observed update-level drift.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- The current pipeline includes non-empty version transitions and can support update-level drift claims.",
                "",
            ]
        )

    lines.extend(
        [
            "## Candidate Distribution",
            "",
            f"- Negative label rows: **{label_counts.get('0', 0):,}**",
            f"- Positive label rows: **{label_counts.get('1', 0):,}**",
            "",
            "## LLM Annotation Snapshot",
            "",
            f"- Annotated high-risk low-disclosure candidates: **{ann_total:,}**",
            f"- Consensus `FALSE_POSITIVE`: **{ann_counts.get('FALSE_POSITIVE', 0):,}**",
            f"- Consensus `UNCLEAR`: **{ann_counts.get('UNCLEAR', 0):,}**",
            f"- Consensus `CONFIRMED`: **{ann_counts.get('CONFIRMED', 0):,}**",
            "",
            "## Research Gaps To Close Next",
            "",
            "1. Build a real version-history collector for `skills.sh` so `transitions.csv` is non-empty.",
            "2. Treat current snapshot positives as proxy candidates rather than ground truth.",
            "3. Manually review the `UNCLEAR` annotation subset before making stronger security claims.",
            "4. Keep detector claims scoped to ranking proxy candidates, not detecting confirmed rug-pull updates.",
            "",
            "## Safe Paper Wording",
            "",
            "> On `skills.sh`, our current evidence is cross-sectional rather than longitudinal: we measure present-day capability/disclosure mismatch and rank SkillRugPull proxy candidates, but we do not yet observe true update-level trust drift because public version-transition data is absent in the archived snapshot.",
            "",
        ]
    )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
