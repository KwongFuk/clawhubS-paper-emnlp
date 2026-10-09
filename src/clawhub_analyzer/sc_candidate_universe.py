"""Build a large runnable SC candidate universe from the P25R seed pool."""

from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from clawhub_analyzer.graph2_pairs import load_graph2_skill_profiles
from clawhub_analyzer.hypergraph import load_hypergraph_skill_profiles
from clawhub_analyzer.p25r_protocol import SUPPORTED_SINKS
from clawhub_analyzer.sc_heuristics import (
    classify_sink_singleton_hazard,
    classify_source_singleton_role,
    composition_support_score,
    infer_coinstall_surface,
    infer_composition_trigger_type,
    infer_joint_invocation_surface,
    signals_from_profile,
    singleton_safety_plausibility,
)
from clawhub_analyzer.sc_stateful_pairs import build_stateful_pair_row


DEFAULT_DB_PATH = Path("data/experiment1_split/merged.sqlite3")
DEFAULT_INPUT_CSV = Path(
    "experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_seed_pool.csv"
)
DEFAULT_OUTPUT_DIR = Path("data/experiment1_split/analysis_sc_stateful")


def methodology_decision(
    *,
    source_role: str,
    sink_hazard: str,
    singleton_score: float,
    support_score: float,
    channel_confidence: float,
    runnable_family: str,
) -> tuple[str, str]:
    if not runnable_family:
        return "reject", "unsupported_sink"
    if source_role == "self_acting":
        return "reject", "source_self_acting"
    if sink_hazard == "likely_unsafe_alone":
        return "reject", "sink_likely_unsafe_alone"
    if channel_confidence < 0.20:
        return "reject", "low_channel_confidence"
    if singleton_score < 0.45:
        return "reject", "low_singleton_support"
    if support_score < 0.55:
        return "reject", "low_composition_support"
    if singleton_score >= 0.60 and support_score >= 0.70 and channel_confidence >= 0.25:
        return "main", ""
    return "expansion", ""


def generate_sc_candidate_universe_artifacts(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    input_csv: Path = DEFAULT_INPUT_CSV,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Path]:
    if not input_csv.exists():
        raise FileNotFoundError(f"missing runnable seed pool: {input_csv}")
    if not db_path.exists():
        raise FileNotFoundError(f"missing SQLite database: {db_path}")

    hyper_profiles = load_hypergraph_skill_profiles(db_path, progress_label="sc-universe")
    hyper_by_id = {profile.skill_id: profile for profile in hyper_profiles}
    graph2_profiles = load_graph2_skill_profiles(db_path)
    graph2_by_id = {profile.skill_id: profile for profile in graph2_profiles}

    with input_csv.open(encoding="utf-8") as handle:
        input_rows = list(csv.DictReader(handle))

    enriched_rows: list[dict[str, str]] = []
    rejection_counter: Counter[str] = Counter()
    band_counter: Counter[str] = Counter()

    for row in input_rows:
        src_h = hyper_by_id.get(row["source_skill_id"])
        sink_h = hyper_by_id.get(row["sink_skill_id"])
        src_g = graph2_by_id.get(row["source_skill_id"])
        sink_g = graph2_by_id.get(row["sink_skill_id"])
        if src_h is None or sink_h is None or src_g is None or sink_g is None:
            rejection_counter["missing_profiles"] += 1
            continue

        source_signal = signals_from_profile(src_h)
        sink_signal = signals_from_profile(sink_h)
        source_role = classify_source_singleton_role(source_signal)
        sink_hazard = classify_sink_singleton_hazard(sink_signal)
        carrier_type = row["carrier_type"]
        coinstall_surface = infer_coinstall_surface(carrier_type, source_signal, sink_signal)
        joint_surface = infer_joint_invocation_surface(carrier_type, source_signal, sink_signal)
        trigger_type = infer_composition_trigger_type(coinstall_surface, joint_surface)
        singleton_score = singleton_safety_plausibility(
            source_role=source_role,
            sink_hazard=sink_hazard,
            channel_confidence=float(row["channel_confidence"]),
            shared_clusters=int(row["shared_clusters"]),
            source_installs=int(row["source_installs_all_time"]),
        )
        runnable_family = SUPPORTED_SINKS.get(row["sink_skill_id"], {}).get("family", "")
        support_score = composition_support_score(
            singleton_score=singleton_score,
            composition_trigger_type=trigger_type,
            coinstall_surface=coinstall_surface,
            channel_confidence=float(row["channel_confidence"]),
            shared_clusters=int(row["shared_clusters"]),
            ecological_pass=(row.get("ecological_pass") == "True"),
            runtime_support_tier=row.get("runtime_support_tier", ""),
        )
        candidate_band, rejection_reason = methodology_decision(
            source_role=source_role,
            sink_hazard=sink_hazard,
            singleton_score=singleton_score,
            support_score=support_score,
            channel_confidence=float(row["channel_confidence"]),
            runnable_family=runnable_family,
        )
        if candidate_band == "reject":
            rejection_counter[rejection_reason] += 1

        base_row = dict(row)
        base_row.update(
            {
                "source_singleton_role": source_role,
                "sink_singleton_hazard": sink_hazard,
                "coinstall_surface": coinstall_surface,
                "joint_invocation_surface": joint_surface,
                "composition_trigger_type": trigger_type,
                "singleton_safety_plausibility": f"{singleton_score:.4f}",
                "composition_support_score": f"{support_score:.4f}",
                "runnable_family": runnable_family,
                "c1_dynamic_candidate": "True" if candidate_band == "main" else "False",
                "c1_exclusion_reason": rejection_reason,
                "sc_candidate_band": candidate_band,
                "sc_methodology_pass": "True" if candidate_band in {"main", "expansion"} else "False",
                "sc_rejection_reason": rejection_reason,
            }
        )
        enriched = build_stateful_pair_row(base_row, graph2_by_id)
        enriched_rows.append(enriched)
        band_counter[candidate_band] += 1

    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "sc_candidate_universe_from_p25r.csv"
    top_csv = output_dir / "sc_candidate_universe_top.csv"
    summary_md = output_dir / "sc_candidate_universe_summary.md"
    audit_json = output_dir / "sc_candidate_universe_audit.json"

    fieldnames = list(enriched_rows[0].keys()) if enriched_rows else []
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched_rows)

    eligible_rows = [row for row in enriched_rows if row["sc_methodology_pass"] == "True"]
    top_rows = sorted(eligible_rows, key=lambda item: float(item["chain_confidence"]), reverse=True)[:500]
    with top_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(top_rows)

    by_family = Counter(row["runnable_family"] for row in eligible_rows)
    by_band = Counter(row["sc_candidate_band"] for row in enriched_rows)
    lines = [
        "# SC Candidate Universe From P25R",
        "",
        "> This file rebuilds the SC runtime universe from the much larger P25R runnable seed pool instead of the smaller C1/CAS table.",
        "",
        "## Overview",
        "",
        f"- input runnable rows: `{len(input_rows)}`",
        f"- extracted rows: `{len(enriched_rows)}`",
        f"- methodology-pass rows: `{len(eligible_rows)}`",
        f"- candidate bands: `{json.dumps(dict(by_band), ensure_ascii=False)}`",
        f"- rejection reasons: `{json.dumps(dict(rejection_counter), ensure_ascii=False)}`",
        "",
        "## Eligible By Family",
        "",
        "| Family | Count |",
        "| --- | ---: |",
    ]
    for family, count in sorted(by_family.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"| `{family}` | {count} |")
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    audit_json.write_text(
        json.dumps(
            {
                "input_rows": len(input_rows),
                "extracted_rows": len(enriched_rows),
                "methodology_pass_rows": len(eligible_rows),
                "candidate_bands": dict(by_band),
                "rejection_reasons": dict(rejection_counter),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "sc_candidate_universe_csv": output_csv,
        "sc_candidate_universe_top_csv": top_csv,
        "sc_candidate_universe_summary_markdown": summary_md,
        "sc_candidate_universe_audit_json": audit_json,
    }
