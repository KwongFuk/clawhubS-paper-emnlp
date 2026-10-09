#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
import sys


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from clawhub_analyzer.c1_wcr import compute_wcr_evidence, load_cape_profiles_for_skill_ids  # noqa: E402


IN_CSV = EXPERIMENT_ROOT / "data/analysis_c1_static/c1_cas_pairs.csv"
DB_PATH = PROJECT_ROOT / "data/experiment1_split/merged.sqlite3"
OUT_DIR = EXPERIMENT_ROOT / "data/analysis_c1_static"
OUT_CSV = OUT_DIR / "c1_cas_wcr_pairs.csv"
OUT_SUMMARY = OUT_DIR / "c1_wcr_summary.md"

WCR_FIELDS = [
    "wcr_shared_channels",
    "wcr_best_channel",
    "wcr_channel_alignment_score",
    "wcr_source_output_type",
    "wcr_sink_trigger_type",
    "wcr_sink_when_spec",
    "wcr_sink_trigger_keywords",
    "wcr_trigger_compatibility",
    "wcr_combined_risk",
    "when_reachability_tier",
    "wcr_score",
    "wcr_mvi_hint",
    "wcr_pass",
    "c1_wcr_dynamic_candidate",
    "c1_wcr_exclusion_reason",
]


def _wcr_candidate_reason(row: dict[str, str]) -> str:
    if not row["runnable_family"]:
        return "unsupported_sink"
    if row["sink_singleton_hazard"] == "likely_unsafe_alone":
        return "sink_likely_unsafe_alone"
    if row["wcr_pass"] != "True":
        return "wcr_not_reached"
    if row["when_reachability_tier"] == "W1_low" and float(row["wcr_channel_alignment_score"]) < 0.40:
        return "weak_trigger_alignment"
    if row["source_singleton_role"] == "self_acting" and float(row["wcr_score"]) < 0.60:
        return "source_self_acting_weak_wcr"
    if float(row["singleton_safety_plausibility"]) < 0.35:
        return "low_singleton_support"
    return ""


def main() -> int:
    if not IN_CSV.exists():
        raise FileNotFoundError(f"missing CAS file: {IN_CSV}")
    if not DB_PATH.exists():
        raise FileNotFoundError(f"missing db: {DB_PATH}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with IN_CSV.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    skill_ids = {
        skill_id
        for row in rows
        for skill_id in (row["source_skill_id"], row["sink_skill_id"])
    }
    cape_profiles = load_cape_profiles_for_skill_ids(DB_PATH, skill_ids)

    missing_profiles = 0
    out_rows: list[dict[str, str]] = []
    for row in rows:
        source_profile = cape_profiles.get(row["source_skill_id"])
        sink_profile = cape_profiles.get(row["sink_skill_id"])
        enriched = dict(row)

        if source_profile is None or sink_profile is None:
            missing_profiles += 1
            enriched.update(
                {
                    "wcr_shared_channels": "",
                    "wcr_best_channel": "",
                    "wcr_channel_alignment_score": "0.0",
                    "wcr_source_output_type": "",
                    "wcr_sink_trigger_type": "",
                    "wcr_sink_when_spec": "",
                    "wcr_sink_trigger_keywords": "",
                    "wcr_trigger_compatibility": "0.0",
                    "wcr_combined_risk": "0.0",
                    "when_reachability_tier": "W0_none",
                    "wcr_score": "0.0",
                    "wcr_mvi_hint": "",
                    "wcr_pass": "False",
                }
            )
        else:
            evidence = compute_wcr_evidence(
                row=row,
                source_profile=source_profile,
                sink_profile=sink_profile,
            )
            if evidence is None:
                enriched.update(
                    {
                        "wcr_shared_channels": "",
                        "wcr_best_channel": "",
                        "wcr_channel_alignment_score": "0.0",
                        "wcr_source_output_type": source_profile.output_type,
                        "wcr_sink_trigger_type": sink_profile.trigger_type,
                        "wcr_sink_when_spec": sink_profile.when_spec[:300],
                        "wcr_sink_trigger_keywords": json.dumps(sink_profile.trigger_keywords[:8], ensure_ascii=False),
                        "wcr_trigger_compatibility": "0.0",
                        "wcr_combined_risk": "0.0",
                        "when_reachability_tier": "W0_none",
                        "wcr_score": "0.0",
                        "wcr_mvi_hint": "",
                        "wcr_pass": "False",
                    }
                )
            else:
                pass_gate = evidence.when_reachability_tier in {"W2_medium", "W3_high"} or (
                    evidence.when_reachability_tier == "W1_low" and evidence.channel_alignment_score >= 0.75
                )
                enriched.update(
                    {
                        "wcr_shared_channels": json.dumps(evidence.shared_channels, ensure_ascii=False),
                        "wcr_best_channel": evidence.best_channel,
                        "wcr_channel_alignment_score": str(evidence.channel_alignment_score),
                        "wcr_source_output_type": evidence.source_output_type,
                        "wcr_sink_trigger_type": evidence.sink_trigger_type,
                        "wcr_sink_when_spec": evidence.sink_when_spec,
                        "wcr_sink_trigger_keywords": json.dumps(evidence.sink_trigger_keywords, ensure_ascii=False),
                        "wcr_trigger_compatibility": str(evidence.trigger_compatibility),
                        "wcr_combined_risk": str(evidence.combined_risk),
                        "when_reachability_tier": evidence.when_reachability_tier,
                        "wcr_score": str(evidence.wcr_score),
                        "wcr_mvi_hint": evidence.minimum_viable_injection,
                        "wcr_pass": "True" if pass_gate else "False",
                    }
                )

        reason = _wcr_candidate_reason(enriched)
        enriched["c1_wcr_dynamic_candidate"] = "True" if not reason else "False"
        enriched["c1_wcr_exclusion_reason"] = reason
        out_rows.append(enriched)

    fieldnames = list(rows[0].keys()) + WCR_FIELDS
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    by_tier = Counter(row["when_reachability_tier"] for row in out_rows)
    by_trigger = Counter(row["wcr_sink_trigger_type"] for row in out_rows if row["wcr_sink_trigger_type"])
    by_candidate = Counter(row["c1_wcr_dynamic_candidate"] for row in out_rows)

    lines = [
        "# C1 WCR Experiment",
        "",
        "> Input: `c1_cas_pairs.csv`",
        "> Goal: add `when-condition reachability` as a new filter layer on top of CAS.",
        "",
        "## Overview",
        "",
        f"- input rows: `{len(rows)}`",
        f"- unique skills profiled: `{len(cape_profiles)}`",
        f"- rows missing CAPE profiles: `{missing_profiles}`",
        f"- WCR candidates kept: `{by_candidate.get('True', 0)}`",
        "",
        "## By WCR Tier",
        "",
        "| Tier | Count |",
        "| --- | ---: |",
    ]
    for tier, count in sorted(by_tier.items()):
        lines.append(f"| `{tier}` | {count} |")
    lines.extend(
        [
            "",
            "## By Sink Trigger Type",
            "",
            "| Trigger Type | Count |",
            "| --- | ---: |",
        ]
    )
    for trigger_type, count in sorted(by_trigger.items()):
        lines.append(f"| `{trigger_type}` | {count} |")

    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "rows": len(out_rows),
                "wcr_candidates": by_candidate.get("True", 0),
                "csv": str(OUT_CSV),
                "summary": str(OUT_SUMMARY),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
