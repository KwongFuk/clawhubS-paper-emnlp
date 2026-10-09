#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
SRC_DIR = PROJECT_ROOT / "src"
import sys

sys.path.insert(0, str(SRC_DIR))

from clawhub_analyzer.p25r_protocol import SUPPORTED_SINKS  # noqa: E402


OUT_DIR = EXPERIMENT_ROOT / "data/analysis_runtime_p25r_500"
OUT_CSV = OUT_DIR / "p25r_seed_pool.csv"
OUT_SUMMARY = OUT_DIR / "p25r_seed_pool_summary.md"
SCRIPT_PATH = EXPERIMENT_ROOT / "scripts/static_filtering/run_bcr_cis_v3.py"


def load_v3_module():
    spec = importlib.util.spec_from_file_location("run_bcr_cis_v3_mod", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def runtime_support_tier(conf: float, src_installs: int, clusters: int, ecological_pass: bool) -> str | None:
    if conf < 0.10 or clusters < 1:
        return None
    if ecological_pass:
        return "tier_a"
    if src_installs >= 20:
        return "tier_b"
    return "tier_c"


def main() -> int:
    mod = load_v3_module()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading skills from {mod.DB_PATH} ...")
    profiles = mod.load_hypergraph_skill_profiles(mod.DB_PATH, progress_label="p25r-seed")
    print(f"  {len(profiles):,} skills loaded")

    print("Loading temporal data ...")
    temporal = mod.load_temporal_data()
    print(f"  {len(temporal):,} timestamps loaded")

    profile_by_id = {p.skill_id: p for p in profiles}
    print("Computing channel coverage and sink index ...")
    channel_coverage = mod.compute_channel_idf(profiles)
    max_raw_exp = max(math.log1p(p.downloads + p.stars) for p in profiles) * 2
    max_exposure = max_raw_exp if max_raw_exp > 0 else 1.0
    max_installs = max((p.installs_all_time for p in profiles), default=1) or 1

    sink_index: dict[str, list[str]] = defaultdict(list)
    for profile in profiles:
        if profile.skill_id not in SUPPORTED_SINKS:
            continue
        if mod.sink_type(profile) is None:
            continue
        for node in mod.activation_carriers(profile):
            sink_index[node].append(profile.skill_id)

    sources = [p for p in profiles if mod.source_type(p) is not None]
    rows: list[dict[str, object]] = []
    reject_counts: Counter[str] = Counter()

    total = len(sources)
    for i, src in enumerate(sources, 1):
        if i % 3000 == 0 or i == total:
            print(f"  Source {i}/{total} | collected: {len(rows):,}", flush=True)

        candidate_sink_ids: set[str] = set()
        for node in mod.emission_carriers(src):
            candidate_sink_ids.update(sink_index.get(node, []))

        for snk_id in candidate_sink_ids:
            snk = profile_by_id[snk_id]
            passed, shared_ch, shared_obj, reason = mod.bcr_pass(src, snk, channel_coverage)
            if not passed:
                reject_counts[reason] += 1
                continue

            carrier = mod.primary_carrier(shared_ch)
            src_evidence = mod.channel_confidence_evidence(src, carrier)
            snk_evidence = mod.channel_confidence_evidence(snk, carrier)
            conf = round((src_evidence.confidence + snk_evidence.confidence) / 2, 3)
            clusters = mod.shared_clusters(mod.pair_skill_text(src), mod.pair_skill_text(snk))
            ecology_score = mod.plausibility_score(
                conf,
                src.installs_all_time,
                snk.installs_all_time,
                clusters,
                max_installs,
            )
            ecology_reason = mod.ecological_filter_reason(
                conf=conf,
                src_installs=src.installs_all_time,
                snk_installs=snk.installs_all_time,
                clusters=clusters,
                min_confidence=mod.MIN_CHANNEL_CONFIDENCE,
                min_source_installs=mod.MIN_SOURCE_INSTALLS,
                min_sink_installs=mod.MIN_SINK_INSTALLS,
            )
            tier = runtime_support_tier(conf, src.installs_all_time, clusters, ecology_reason is None)
            if tier is None:
                continue

            src_temporal = temporal.get(src.skill_id, {})
            snk_temporal = temporal.get(snk.skill_id, {})
            src_age = mod.parse_age_days(src_temporal.get("created_at", ""))
            snk_age = mod.parse_age_days(snk_temporal.get("created_at", ""))
            pair_recency_days = min((x for x in (src_age, snk_age) if x is not None), default=None)

            row = {
                "source_skill_id": src.skill_id,
                "source_name": src.name,
                "sink_skill_id": snk.skill_id,
                "sink_name": snk.name,
                "carrier_type": carrier,
                "source_type": mod.source_type(src),
                "sink_type": mod.sink_type(snk),
                "source_domain": mod.primary_domain(src),
                "sink_domain": mod.primary_domain(snk),
                "cis_tier": f"{mod.SINK_SEV.get(mod.sink_type(snk), 'medium')}×"
                f"{'cross-domain' if mod.primary_domain(src) != mod.primary_domain(snk) else 'same-domain'}",
                "ph_score": mod.ph_score_normalized(src, snk, carrier, max_exposure),
                "channel_confidence": conf,
                "source_channel_support_count": src_evidence.support_count,
                "sink_channel_support_count": snk_evidence.support_count,
                "source_channel_support_signals": "|".join(src_evidence.matched_signals),
                "sink_channel_support_signals": "|".join(snk_evidence.matched_signals),
                "source_installs_all_time": src.installs_all_time,
                "sink_installs_all_time": snk.installs_all_time,
                "shared_clusters": clusters,
                "plausibility_score": ecology_score,
                "ecological_pass": ecology_reason is None,
                "ecological_reason": ecology_reason or "",
                "runtime_support_tier": tier,
                "runtime_seed_reason": "supported_sink_bcr_pair",
                "shared_channels": "|".join(sorted(shared_ch)),
                "shared_objects": "|".join(sorted(shared_obj)),
                "source_downloads": src.downloads,
                "sink_downloads": snk.downloads,
                "pair_recency_days": round(pair_recency_days, 1) if pair_recency_days is not None else "",
            }
            rows.append(row)

    def sort_key(item: dict[str, object]) -> tuple[float, float, float, float, str, str]:
        tier_rank = {"tier_a": 0, "tier_b": 1, "tier_c": 2}.get(str(item["runtime_support_tier"]), 9)
        return (
            tier_rank,
            -float(item["plausibility_score"]),
            -float(item["channel_confidence"]),
            -float(item["ph_score"]),
            str(item["sink_skill_id"]),
            str(item["source_skill_id"]),
        )

    rows.sort(key=sort_key)

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    by_sink = Counter(row["sink_skill_id"] for row in rows)
    by_tier = Counter(row["runtime_support_tier"] for row in rows)
    by_carrier = Counter(row["carrier_type"] for row in rows)
    lines = [
        "# P25R Seed Pool",
        "",
        "> Source: full latest-static hypergraph scan restricted to runnable sink families",
        "",
        "## Overview",
        "",
        f"- total seed rows: `{len(rows)}`",
        f"- supported sinks covered: `{len(by_sink)}`",
        f"- reject counts inside supported-sink scan: `{json.dumps(dict(reject_counts), ensure_ascii=False)}`",
        "",
        "## By Sink",
        "",
        "| Sink | Seed Rows |",
        "|---|---:|",
    ]
    for sink_id, count in by_sink.items():
        lines.append(f"| `{sink_id}` | {count} |")
    lines.extend(
        [
            "",
            "## By Runtime Support Tier",
            "",
            "| Tier | Count | Meaning |",
            "|---|---:|---|",
            f"| `tier_a` | {by_tier.get('tier_a', 0)} | full ecological pass |",
            f"| `tier_b` | {by_tier.get('tier_b', 0)} | conf+cluster pass, lower source installs |",
            f"| `tier_c` | {by_tier.get('tier_c', 0)} | conf+cluster pass, weakest source support |",
            "",
            "## Top Carriers",
            "",
            "| Carrier | Count |",
            "|---|---:|",
        ]
    )
    for carrier, count in by_carrier.most_common(10):
        lines.append(f"| `{carrier}` | {count} |")
    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"seed_rows": len(rows), "seed_csv": str(OUT_CSV), "summary": str(OUT_SUMMARY)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
