#!/usr/bin/env python3
"""BCR+CIS v3 — Full optimization pass on all 6 dimensions.

Optimizations over v2 (run_bcr_cis_optimized.py):

  Opt-A  Credential sub-typing: credential_state pairs require SAME author-domain
         prefix OR object node more specific than just 'private_content'
  Opt-B  Source diversity cap: final top-10k caps each source at MAX_PER_SOURCE rows
  Opt-C  PH normalization: each of 3 PH components normalized to [0,1] before weighting
  Opt-D  Stratified output: separate CSVs per CIS tier (critical/high/medium × domain)
  Opt-E  Channel attribution confidence proxy: count how many of 5 text signals
         (description, tags, metadata_systems, install_command, skill_id) support the channel;
         pairs with confidence_score < MIN_CHANNEL_CONFIDENCE are deprioritized
  Opt-F  Time dimension: for each pair, record recency = min(src_age, snk_age) in days;
         annotate pairs added in the last 90 days as 'new_arrival' for novelty analysis
  Opt-G  Ecological plausibility: shared semantic clusters plus install breadth
         are emitted in the mainline output and used as a ranking tie-break
"""

from __future__ import annotations

import csv
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from clawhub_analyzer.hypergraph import HypergraphSkillProfile, load_hypergraph_skill_profiles  # noqa: E402
from clawhub_analyzer.pair_plausibility import (  # noqa: E402
    channel_confidence_evidence,
    ecological_filter_reason,
    pair_skill_text,
    plausibility_score,
    shared_clusters,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_PATH = PROJECT_ROOT / "data/experiment1_split/merged.sqlite3"
OUT_DIR = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# BCR constants
# ---------------------------------------------------------------------------
BCR_SOURCE_TYPES = {"untrusted_input", "sensitive_read", "contaminated_state"}
BCR_SINK_TYPES   = {"execute", "modify", "delete", "publish", "control", "transact"}
GENERIC_CHANNELS = {"semantic_channel:transform_buffer", "system_channel:time"}
HIGH_COVERAGE_THRESHOLD = 0.30

# Opt-A: credential_state pairs additionally require at least one of:
#   - shared author prefix (e.g., both byungkyu/*)
#   - OR shared object node more specific than 'object:private_content'
GENERIC_OBJECTS = {"object:private_content", "object:text", "object:document"}

# CIS severity
SINK_SEV = {
    "transact": "critical", "control": "critical", "delete": "critical",
    "execute": "high", "modify": "high", "publish": "medium",
}

# PH persistence bonus
PERSISTENT_CHANNELS = {
    "workspace", "memory", "credential_state", "browser_state",
    "device_signal", "desktop_state", "audio_capture", "video_capture",
}

# Opt-B: source diversity cap in final output
MAX_PER_SOURCE = 30
MAX_OUTPUT_PAIRS = 10_000

# Opt-E: minimum channel confidence score to be included in main output
# (pairs below this are still recorded but flagged)
MIN_CHANNEL_CONFIDENCE = 0.25  # at least 2 of 5 signals across the pair on average

# Opt-F: recency threshold in days
RECENCY_DAYS = 90
NOW = datetime.now(timezone.utc)

# Per-source pairing cap during collection
MAX_SINKS_PER_SOURCE = 50

# Opt-G: ecological plausibility thresholds
MIN_SOURCE_INSTALLS = 200
MIN_SINK_INSTALLS = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def source_type(p: HypergraphSkillProfile) -> str | None:
    present = {n.split(":", 1)[1] for n in p.source_nodes}
    for t in ("contaminated_state", "sensitive_read", "untrusted_input"):
        if t in present:
            return t
    return None


def sink_type(p: HypergraphSkillProfile) -> str | None:
    present = {n.split(":", 1)[1] for n in p.sink_nodes}
    for t in ("transact", "delete", "control", "execute", "modify", "publish"):
        if t in present:
            return t
    return None


def primary_domain(p: HypergraphSkillProfile) -> str:
    present = {n.split(":", 1)[1] for n in p.domain_nodes}
    for d in ("External", "EnterpriseInternal", "AgentWorkspace",
              "LocalHost", "ThirdPartySaaS", "PhysicalDevice", "UserProvided"):
        if d in present:
            return d
    return "Unknown"


def emission_carriers(p: HypergraphSkillProfile) -> frozenset[str]:
    return frozenset(
        n for n in p.emission_channel_nodes()
        if (n.startswith("semantic_channel:") or n.startswith("system_channel:"))
        and n not in GENERIC_CHANNELS
    )


def activation_carriers(p: HypergraphSkillProfile) -> frozenset[str]:
    return frozenset(
        n for n in p.activation_channel_nodes()
        if (n.startswith("semantic_channel:") or n.startswith("system_channel:"))
        and n not in GENERIC_CHANNELS
    )


def emission_objects(p: HypergraphSkillProfile) -> frozenset[str]:
    return frozenset(n for n in p.emission_edge.head if n.startswith("object:"))


def activation_objects(p: HypergraphSkillProfile) -> frozenset[str]:
    return frozenset(n for n in p.activation_edge.tail if n.startswith("object:"))


def primary_carrier(shared: frozenset[str]) -> str:
    PRIORITY = [
        "semantic_channel:memory", "semantic_channel:workspace",
        "system_channel:credential_state", "system_channel:browser_state",
        "system_channel:audio_capture", "system_channel:video_capture",
        "system_channel:device_signal", "system_channel:keyboard_io",
        "system_channel:mouse_io", "system_channel:screen_capture",
        "system_channel:desktop_state",
    ]
    for pr in PRIORITY:
        if pr in shared:
            return pr.split(":", 1)[1]
    return next(iter(sorted(shared))).split(":", 1)[1]


def author_prefix(skill_id: str) -> str:
    """Return the author handle from 'author/skill-name'."""
    return skill_id.split("/")[0] if "/" in skill_id else skill_id


def compute_channel_idf(profiles: list[HypergraphSkillProfile]) -> dict[str, float]:
    total = len(profiles)
    counts: Counter[str] = Counter()
    for p in profiles:
        for n in emission_carriers(p):
            counts[n] += 1
    return {ch: count / total for ch, count in counts.items()}


def load_temporal_data() -> dict[str, dict]:
    """Load created_at per skill_id from the DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT skill_id, created_at, updated_at FROM skills").fetchall()
    conn.close()
    result = {}
    for row in rows:
        result[row["skill_id"]] = {
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }
    return result


def parse_age_days(date_str: str) -> float | None:
    """Return age in days from a date string, or None if unparseable."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:19], fmt[:len(fmt.split("%z")[0])])
            dt = dt.replace(tzinfo=timezone.utc)
            return (NOW - dt).total_seconds() / 86400
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Opt-C: Normalized PH score
# ---------------------------------------------------------------------------

def ph_score_normalized(
    src: HypergraphSkillProfile,
    snk: HypergraphSkillProfile,
    carrier: str,
    max_exposure: float,
) -> float:
    """Normalized PH: each component in [0,1] independently, then sum."""
    raw_exposure = math.log1p(src.downloads + snk.downloads + src.stars + snk.stars)
    exp_component = raw_exposure / max_exposure if max_exposure > 0 else 0.0

    persistence = 1.0 if carrier in PERSISTENT_CHANNELS else 0.0

    arity = len(snk.activation_edge.tail)
    arity_component = max(0.0, 1.0 - arity / 20.0)

    return round(exp_component + persistence + arity_component, 4)


# ---------------------------------------------------------------------------
# Sink index
# ---------------------------------------------------------------------------

def build_sink_index(profiles: list[HypergraphSkillProfile]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for p in profiles:
        if sink_type(p) is None:
            continue
        for n in activation_carriers(p):
            index[n].append(p.skill_id)
    return index


# ---------------------------------------------------------------------------
# BCR pass with all filters
# ---------------------------------------------------------------------------

def bcr_pass(
    src: HypergraphSkillProfile,
    snk: HypergraphSkillProfile,
    channel_coverage: dict[str, float],
) -> tuple[bool, frozenset[str], frozenset[str], str | None]:
    """Returns (pass, shared_channels, shared_objects, rejection_reason)."""
    # Same-author filter
    if (src.author_handle and snk.author_handle
            and src.author_handle == snk.author_handle):
        return False, frozenset(), frozenset(), "same_author"

    shared_ch = emission_carriers(src) & activation_carriers(snk)
    if not shared_ch:
        return False, frozenset(), frozenset(), "no_channel_overlap"

    shared_obj = emission_objects(src) & activation_objects(snk)

    # IDF + corroboration check
    all_high_cov = all(channel_coverage.get(ch, 0.0) > HIGH_COVERAGE_THRESHOLD for ch in shared_ch)
    if all_high_cov and not shared_obj:
        return False, frozenset(), frozenset(), "idf_no_object"

    # Opt-A: credential_state subtype specificity
    credential_only = all("credential_state" in ch for ch in shared_ch)
    if credential_only:
        # Check for same author prefix OR specific (non-generic) shared object
        same_prefix = author_prefix(src.skill_id) == author_prefix(snk.skill_id)
        specific_obj = shared_obj - GENERIC_OBJECTS
        if not same_prefix and not specific_obj:
            return False, frozenset(), frozenset(), "credential_no_specificity"

    return True, shared_ch, shared_obj, None


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_v3(
    profiles: list[HypergraphSkillProfile],
    temporal: dict[str, dict],
) -> tuple[list[dict], dict]:
    profile_by_id = {p.skill_id: p for p in profiles}

    print("  Computing channel IDF ...")
    channel_coverage = compute_channel_idf(profiles)

    # Opt-C: pre-compute max_exposure for normalization
    max_raw_exp = max(
        math.log1p(p.downloads + p.stars) for p in profiles
    ) * 2  # pair max ≈ 2× single max
    max_exposure = max_raw_exp if max_raw_exp > 0 else 1.0
    max_installs = max((p.installs_all_time for p in profiles), default=1) or 1

    sink_index = build_sink_index(profiles)
    sources = [p for p in profiles if source_type(p) is not None]
    print(f"  Source candidates: {len(sources):,}")

    sev_order = {"critical": 0, "high": 1, "medium": 2}
    dom_order = {"cross-domain": 0, "same-domain": 1}

    all_results: list[dict] = []
    reject_counts: Counter[str] = Counter()
    total = len(sources)

    for i, src in enumerate(sources, 1):
        if i % 3000 == 0 or i == total:
            print(f"  Source {i}/{total} | collected: {len(all_results):,}", flush=True)

        stype = source_type(src)
        src_domain = primary_domain(src)
        src_ch = emission_carriers(src)

        candidates: set[str] = set()
        for n in src_ch:
            candidates.update(sink_index.get(n, []))

        per_source: list[dict] = []

        for snk_id in candidates:
            if snk_id == src.skill_id:
                continue
            snk = profile_by_id.get(snk_id)
            if snk is None:
                continue
            st = sink_type(snk)
            if st is None:
                continue

            passed, shared_ch, shared_obj, reason = bcr_pass(src, snk, channel_coverage)
            if not passed:
                reject_counts[reason] += 1
                continue

            carrier = primary_carrier(shared_ch)
            snk_domain = primary_domain(snk)
            domain_cross = "cross-domain" if src_domain != snk_domain else "same-domain"
            sink_sev = SINK_SEV.get(st, "medium")
            ph = ph_score_normalized(src, snk, carrier, max_exposure)

            # Opt-E: channel confidence
            src_evidence = channel_confidence_evidence(src, carrier)
            snk_evidence = channel_confidence_evidence(snk, carrier)
            ch_confidence = round((src_evidence.confidence + snk_evidence.confidence) / 2, 3)

            # Opt-G: ecological plausibility
            clusters = shared_clusters(pair_skill_text(src), pair_skill_text(snk))
            ecology_score = plausibility_score(
                ch_confidence,
                src.installs_all_time,
                snk.installs_all_time,
                clusters,
                max_installs,
            )
            ecology_reason = ecological_filter_reason(
                conf=ch_confidence,
                src_installs=src.installs_all_time,
                snk_installs=snk.installs_all_time,
                clusters=clusters,
                min_confidence=MIN_CHANNEL_CONFIDENCE,
                min_source_installs=MIN_SOURCE_INSTALLS,
                min_sink_installs=MIN_SINK_INSTALLS,
            )

            # Opt-F: temporal
            src_temporal = temporal.get(src.skill_id, {})
            snk_temporal = temporal.get(snk.skill_id, {})
            src_age = parse_age_days(src_temporal.get("created_at", ""))
            snk_age = parse_age_days(snk_temporal.get("created_at", ""))
            pair_recency_days = min(
                (x for x in [src_age, snk_age] if x is not None),
                default=None
            )
            is_new_arrival = (pair_recency_days is not None
                              and pair_recency_days <= RECENCY_DAYS)

            per_source.append({
                "source_skill_id":     src.skill_id,
                "source_name":         src.name,
                "sink_skill_id":       snk.skill_id,
                "sink_name":           snk.name,
                "carrier_type":        carrier,
                "source_type":         stype,
                "sink_type":           st,
                "source_domain":       src_domain,
                "sink_domain":         snk_domain,
                "cis_sink_severity":   sink_sev,
                "cis_domain_crossing": domain_cross,
                "cis_tier":            f"{sink_sev}×{domain_cross}",
                "ph_score":            ph,
                "channel_confidence":  ch_confidence,
                "source_channel_support_count": src_evidence.support_count,
                "sink_channel_support_count": snk_evidence.support_count,
                "source_channel_support_signals": "|".join(src_evidence.matched_signals),
                "sink_channel_support_signals": "|".join(snk_evidence.matched_signals),
                "is_new_arrival":      is_new_arrival,
                "pair_recency_days":   round(pair_recency_days, 1) if pair_recency_days else "",
                "source_installs_all_time": src.installs_all_time,
                "sink_installs_all_time": snk.installs_all_time,
                "shared_clusters":     clusters,
                "plausibility_score":  ecology_score,
                "ecological_pass":     ecology_reason is None,
                "ecological_reason":   ecology_reason or "",
                "shared_channels":     "|".join(sorted(shared_ch)),
                "shared_objects":      "|".join(sorted(shared_obj)),
                "source_downloads":    src.downloads,
                "sink_downloads":      snk.downloads,
            })

        per_source.sort(key=lambda r: (
            sev_order.get(r["cis_sink_severity"], 9),
            dom_order.get(r["cis_domain_crossing"], 9),
            -r["plausibility_score"],
            -r["ph_score"],
        ))
        all_results.extend(per_source[:MAX_SINKS_PER_SOURCE])

    print(f"  Intermediate pairs: {len(all_results):,}")
    print(f"  Rejection counts: {dict(reject_counts.most_common())}")

    # Global sort
    all_results.sort(key=lambda r: (
        sev_order.get(r["cis_sink_severity"], 9),
        dom_order.get(r["cis_domain_crossing"], 9),
        -r["plausibility_score"],
        -r["ph_score"],
    ))

    # Opt-B: source diversity cap
    source_counts: Counter[str] = Counter()
    diverse_results: list[dict] = []
    for r in all_results:
        src_id = r["source_skill_id"]
        if source_counts[src_id] >= MAX_PER_SOURCE:
            continue
        source_counts[src_id] += 1
        diverse_results.append(r)
        if len(diverse_results) >= MAX_OUTPUT_PAIRS:
            break

    print(f"  After source diversity cap: {len(diverse_results):,} pairs "
          f"across {len(source_counts):,} unique sources")

    stats = {
        "total_intermediate": len(all_results),
        "total_output": len(diverse_results),
        "unique_sources": len(source_counts),
        "reject_counts": dict(reject_counts),
        "new_arrivals": sum(1 for r in diverse_results if r["is_new_arrival"]),
        "low_confidence": sum(1 for r in diverse_results
                              if r["channel_confidence"] < MIN_CHANNEL_CONFIDENCE),
        "ecologically_supported": sum(1 for r in diverse_results if r["ecological_pass"]),
    }

    return diverse_results, stats


# ---------------------------------------------------------------------------
# Opt-D: Stratified output
# ---------------------------------------------------------------------------

def write_stratified(results: list[dict]) -> None:
    tiers = ["critical×cross-domain", "critical×same-domain",
             "high×cross-domain", "high×same-domain",
             "medium×cross-domain", "medium×same-domain"]
    for tier in tiers:
        subset = [r for r in results if r["cis_tier"] == tier]
        if not subset:
            continue
        tier_slug = tier.replace("×", "_x_").replace("-", "_")
        out_path = OUT_DIR / f"tier_{tier_slug}.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(subset[0].keys()))
            writer.writeheader()
            writer.writerows(subset)
        print(f"  Tier {tier}: {len(subset):,} pairs → {out_path.name}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def write_summary(results: list[dict], stats: dict, total_skills: int) -> None:
    by_tier    = Counter(r["cis_tier"] for r in results)
    by_carrier = Counter(r["carrier_type"] for r in results)
    crit_cross = [r for r in results if r["cis_tier"] == "critical×cross-domain"]
    new_arr    = [r for r in results if r["is_new_arrival"]]
    low_conf   = [r for r in results if r["channel_confidence"] < MIN_CHANNEL_CONFIDENCE]
    eco_pass   = [r for r in results if r["ecological_pass"]]

    lines = [
        "# BCR+CIS v3 Full Analysis",
        "",
        "> All 6 optimizations active: credential sub-typing | source diversity | "
        "PH normalization | stratified output | channel confidence | time dimension",
        "",
        "## Overview",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total skills | {total_skills:,} |",
        f"| Unique sources in output | {stats['unique_sources']:,} |",
        f"| Output pairs (post-diversity cap) | {len(results):,} |",
        f"| critical × cross-domain | {len(crit_cross):,} |",
        f"| New arrivals (≤{RECENCY_DAYS}d) | {stats['new_arrivals']:,} |",
        f"| Low channel confidence (<{MIN_CHANNEL_CONFIDENCE}) | {low_conf.__len__():,} |",
        f"| Ecologically supported (clusters + installs + confidence) | {len(eco_pass):,} |",
        "",
        "## Rejection Analysis",
        "",
        "| Reason | Count |",
        "|---|---:|",
    ]
    for reason, count in sorted(stats["reject_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"| `{reason}` | {count:,} |")

    lines += [
        "",
        "## CIS Tier Breakdown",
        "",
        "| CIS Tier | Count |",
        "|---|---:|",
    ]
    for tier, count in sorted(by_tier.items()):
        lines.append(f"| `{tier}` | {count} |")

    lines += [
        "",
        "## Carrier Distribution",
        "",
        "| Carrier | Count |",
        "|---|---:|",
    ]
    for ch, count in by_carrier.most_common(15):
        lines.append(f"| `{ch}` | {count} |")

    lines += [
        "",
        "## Ecological Plausibility",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Pairs with `shared_clusters >= 1` | {sum(1 for r in results if r['shared_clusters'] >= 1):,} |",
        f"| Pairs passing ecological screen | {len(eco_pass):,} |",
        "",
        "## New Arrivals: critical×cross-domain pairs (≤90 days old)",
        "",
        "| Source | Sink | Carrier | Sink Type | Recency (days) | CH Confidence |",
        "|---|---|---|---|---:|---:|",
    ]
    new_crit = [r for r in new_arr if r["cis_tier"] == "critical×cross-domain"][:30]
    for r in new_crit:
        lines.append(
            f"| `{r['source_skill_id']}` | `{r['sink_skill_id']}` "
            f"| `{r['carrier_type']}` | `{r['sink_type']}` "
            f"| {r['pair_recency_days']} | {r['channel_confidence']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Top 60: critical × cross-domain (post-diversity cap)",
        "",
        "| Rank | Source | Sink | Carrier | Sink Type | Src Domain | Sink Domain | Plausibility | PH | CH Conf | New? |",
        "|---:|---|---|---|---|---|---|---:|---:|---:|:---:|",
    ]
    for rank, r in enumerate(crit_cross[:60], 1):
        new_flag = "🆕" if r["is_new_arrival"] else ""
        lines.append(
            f"| {rank} | `{r['source_skill_id']}` | `{r['sink_skill_id']}` "
            f"| `{r['carrier_type']}` | `{r['sink_type']}` "
            f"| `{r['source_domain']}` | `{r['sink_domain']}` "
            f"| {r['plausibility_score']} | {r['ph_score']} | {r['channel_confidence']} | {new_flag} |"
        )

    if len(crit_cross) > 60:
        lines.append(f"\n_{len(crit_cross) - 60} more in tier_critical_x_cross_domain.csv_")

    (OUT_DIR / "bcr_cis_v3_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    print(f"Loading skills from {DB_PATH} ...")
    profiles = load_hypergraph_skill_profiles(DB_PATH, progress_label="load")
    print(f"  {len(profiles):,} skills loaded")

    print("Loading temporal data ...")
    temporal = load_temporal_data()
    print(f"  {len(temporal):,} skill timestamps loaded")

    print("\nRunning BCR+CIS v3 analysis ...")
    results, stats = run_v3(profiles, temporal)

    print("\nWriting stratified outputs (Opt-D) ...")
    write_stratified(results)

    print("Writing main CSV ...")
    out_csv = OUT_DIR / "bcr_cis_v3_pairs.csv"
    if results:
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)

    print("Writing summary ...")
    write_summary(results, stats, len(profiles))

    print(f"\nDone. Output: {OUT_DIR}")
    crit = sum(1 for r in results if r["cis_tier"] == "critical×cross-domain")
    print(f"  critical×cross-domain: {crit:,}")
    print(f"  unique sources: {stats['unique_sources']:,}")
    print(f"  new arrivals (≤{RECENCY_DAYS}d): {stats['new_arrivals']:,}")
    print(f"  ecological screen pass: {stats['ecologically_supported']:,}")


if __name__ == "__main__":
    main()
