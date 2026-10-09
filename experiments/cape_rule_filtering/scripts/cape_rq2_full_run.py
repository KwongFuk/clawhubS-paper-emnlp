"""
CAPE RQ2 Full Corpus Run
Extracts capabilities from all 18K ClawHub skills and computes toxic pairs.
Output: results/cape_rq2_results.json + results/cape_rq2_profiles.jsonl
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from clawhub_analyzer.cape import (
    find_toxic_pairs,
    stream_clawhub_profiles,
)
from clawhub_analyzer.cape.types import SkillProfile, ToxicPair

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]

DB_PATH    = str(REPO_ROOT / "data/experiment1_split/merged.sqlite3")
OUT_DIR    = CAPE_ROOT / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PROFILES_JSONL = OUT_DIR / "cape_rq2_profiles.jsonl"
RESULTS_JSON   = OUT_DIR / "cape_rq2_results.json"
PAIRS_JSONL    = OUT_DIR / "cape_rq2_pairs.jsonl"


def profile_to_dict(p: SkillProfile) -> dict:
    return {
        "skill_id":    p.skill_id,
        "name":        p.name,
        "downloads":   p.downloads,
        "author":      p.author,
        "source_caps": sorted(p.source_labels()),
        "sink_caps":   sorted(p.sink_labels()),
        "channel_caps":sorted(p.channel_labels() - {"same_turn_ctx"}),  # exclude structural
        "cap_count":   p.capability_count(),
        "is_dual_role":p.is_dual_role(),
    }


def pair_to_dict(pair: ToxicPair) -> dict:
    return {
        "skill_a_id":      pair.skill_a.skill_id,
        "skill_a_name":    pair.skill_a.name,
        "skill_a_dl":      pair.skill_a.downloads,
        "skill_b_id":      pair.skill_b.skill_id,
        "skill_b_name":    pair.skill_b.name,
        "skill_b_dl":      pair.skill_b.downloads,
        "source_labels":   sorted(pair.a_source_labels),
        "sink_labels":     sorted(pair.b_sink_labels),
        "channels":        sorted(pair.shared_channels),
        "explicit_channel":pair.has_explicit_channel,
        "attack_surface":  pair.attack_surface,
        "combined_downloads": pair.skill_a.downloads + pair.skill_b.downloads,
    }


def main() -> None:
    t0 = time.time()

    # ── 1. Stream all profiles ─────────────────────────────────────────────────
    log.info("Loading all ClawHub skills from %s …", DB_PATH)
    profiles: list[SkillProfile] = []

    with PROFILES_JSONL.open("w") as fout:
        for i, p in enumerate(stream_clawhub_profiles(DB_PATH), 1):
            profiles.append(p)
            fout.write(json.dumps(profile_to_dict(p)) + "\n")
            if i % 1000 == 0:
                elapsed = time.time() - t0
                log.info("  %d skills processed (%.0fs elapsed)", i, elapsed)

    log.info("Total skills loaded: %d", len(profiles))

    # ── 2. Capability statistics ───────────────────────────────────────────────
    n = len(profiles)
    n_source   = sum(1 for p in profiles if p.source_caps())
    n_sink     = sum(1 for p in profiles if p.sink_caps())
    n_dual     = sum(1 for p in profiles if p.is_dual_role())
    n_ch_expl  = sum(1 for p in profiles if any(
        c.label != "same_turn_ctx" for c in p.channel_caps()))

    label_counter: Counter[tuple[str, str]] = Counter()
    for p in profiles:
        for c in p.capabilities:
            if c.label != "same_turn_ctx":
                label_counter[(c.cap_type, c.label)] += 1

    cap_dist = {
        f"{ct}:{lbl}": cnt
        for (ct, lbl), cnt in label_counter.most_common()
    }

    log.info("Source-capable:   %d (%.1f%%)", n_source, 100*n_source/n)
    log.info("Sink-capable:     %d (%.1f%%)", n_sink,   100*n_sink/n)
    log.info("Dual-role:        %d (%.1f%%)", n_dual,   100*n_dual/n)
    log.info("Explicit channel: %d (%.1f%%)", n_ch_expl,100*n_ch_expl/n)

    # ── 3. Find toxic pairs ────────────────────────────────────────────────────
    log.info("Finding toxic pairs (all channels, incl. same_turn_ctx) …")
    pairs_all = find_toxic_pairs(profiles, require_explicit_channel=False)
    log.info("  All pairs: %d", len(pairs_all))

    log.info("Finding toxic pairs (explicit channel only) …")
    pairs_explicit = find_toxic_pairs(profiles, require_explicit_channel=True)
    log.info("  Explicit-channel pairs: %d", len(pairs_explicit))

    # ── 4. Source×Sink breakdown ───────────────────────────────────────────────
    breakdown: Counter[str] = Counter()
    for pair in pairs_all:
        for src in pair.a_source_labels:
            for snk in pair.b_sink_labels:
                breakdown[f"{src} → {snk}"] += 1

    breakdown_explicit: Counter[str] = Counter()
    for pair in pairs_explicit:
        for src in pair.a_source_labels:
            for snk in pair.b_sink_labels:
                breakdown_explicit[f"{src} → {snk}"] += 1

    # ── 5. Save top pairs ─────────────────────────────────────────────────────
    log.info("Saving top 10,000 pairs …")
    with PAIRS_JSONL.open("w") as fout:
        for pair in pairs_all[:10_000]:
            fout.write(json.dumps(pair_to_dict(pair)) + "\n")

    # ── 6. Save summary report ────────────────────────────────────────────────
    elapsed = time.time() - t0
    report = {
        "corpus": "clawhub",
        "n_skills": n,
        "elapsed_seconds": round(elapsed, 1),
        "capability_stats": {
            "n_source_capable":   n_source,
            "pct_source_capable": round(100*n_source/n, 1),
            "n_sink_capable":     n_sink,
            "pct_sink_capable":   round(100*n_sink/n, 1),
            "n_dual_role":        n_dual,
            "pct_dual_role":      round(100*n_dual/n, 1),
            "n_explicit_channel": n_ch_expl,
            "pct_explicit_channel": round(100*n_ch_expl/n, 1),
        },
        "capability_distribution": cap_dist,
        "toxic_pairs": {
            "all_pairs_via_same_turn_ctx": len(pairs_all),
            "explicit_channel_pairs":      len(pairs_explicit),
        },
        "source_sink_breakdown_top20": dict(breakdown.most_common(20)),
        "explicit_breakdown_top20":    dict(breakdown_explicit.most_common(20)),
        "top10_pairs_by_downloads": [
            pair_to_dict(p) for p in pairs_all[:10]
        ],
        "top10_explicit_pairs": [
            pair_to_dict(p) for p in pairs_explicit[:10]
        ],
    }

    with RESULTS_JSON.open("w") as f:
        json.dump(report, f, indent=2)

    log.info("=" * 60)
    log.info("DONE in %.0fs", elapsed)
    log.info("Results: %s", RESULTS_JSON)
    log.info("Profiles: %s", PROFILES_JSONL)
    log.info("Top pairs: %s", PAIRS_JSONL)
    log.info("=" * 60)
    log.info("KEY NUMBERS:")
    log.info("  N skills           = %d", n)
    log.info("  Source-capable     = %d (%.1f%%)", n_source, 100*n_source/n)
    log.info("  Sink-capable       = %d (%.1f%%)", n_sink,   100*n_sink/n)
    log.info("  Dual-role          = %d (%.1f%%)", n_dual,   100*n_dual/n)
    log.info("  All toxic pairs    = %d", len(pairs_all))
    log.info("  Explicit-ch pairs  = %d", len(pairs_explicit))


if __name__ == "__main__":
    main()
