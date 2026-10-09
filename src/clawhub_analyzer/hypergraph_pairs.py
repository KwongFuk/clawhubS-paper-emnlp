"""Stage 2 pairwise hypergraph risk modeling for two-skill compositions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
import math
from pathlib import Path

from clawhub_analyzer.hypergraph import (
    CHANNEL_BASE_PRIORS,
    MAX_JOIN_FANOUT,
    DirectedHypergraph,
    HypergraphChannelInventory,
    HypergraphSkillProfile,
    build_directed_skill_hypergraph,
    load_hypergraph_skill_profiles,
    summarize_hypergraph_channel_inventory,
    write_hypergraph_channel_inventory_csv,
    _build_activation_index,
    _candidate_profiles,
    _carrier_prevalence,
    _carrier_score,
    _channel_subtype,
    _combination_category,
    _mean,
    _risk_outcome,
    _schema_name,
    _severity_tier,
    _sink_impact_prior,
    _source_risk_prior,
    _unique_sorted,
    _weighted_overlap,
)


GENERIC_PAIR_SHARED_NODES = {
    "semantic_channel:transform_buffer",
    "object:schedule_event",
    "system_channel:time",
    "source:contaminated_state",
}

GENERIC_CREDENTIAL_SHARED_NODES = {
    "system_channel:credential_state",
    "system_channel:time",
    "object:schedule_event",
    "object:private_content",
    "source:sensitive_read",
}

GENERIC_CREDENTIAL_BUFFER_SHARED_NODES = {
    "semantic_channel:transform_buffer",
    "system_channel:credential_state",
}

GENERIC_MEMORY_SHARED_NODES = {
    "semantic_channel:memory",
    "source:contaminated_state",
}

GENERIC_POPUP_SHARED_NODES = {
    "system_channel:popup_dialog",
    "object:runtime_signal",
}

SEVERITY_LEVELS = ("low", "medium", "high", "critical")
SEVERITY_RANKS = {level: index for index, level in enumerate(SEVERITY_LEVELS)}


@dataclass(frozen=True)
class HypergraphRiskPair:
    category: str
    schema_name: str
    risk_outcome: str
    severity_tier: str
    source_type: str
    sink_type: str
    source_skill_id: str
    source_name: str
    source_domain: str
    sink_skill_id: str
    sink_name: str
    sink_domain: str
    channel_family: str
    carrier_type: str
    cross_domain: bool
    author_diversity: int
    source_sink_overlap: float
    semantic_overlap: float
    coordination_ease: float
    carrier_score: float
    authority_score: float
    impact_score: float
    source_score: float
    exposure_score: float
    audit_gap_score: float
    risk_score: float
    shared_nodes: tuple[str, ...]
    rationale: str


def _informative_shared_nodes(shared_nodes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(node for node in shared_nodes if not node.startswith("domain:"))


def _is_generic_pair_overlap(shared_nodes: tuple[str, ...]) -> bool:
    informative = set(_informative_shared_nodes(shared_nodes))
    return bool(informative) and (
        informative <= GENERIC_PAIR_SHARED_NODES
        or informative <= GENERIC_CREDENTIAL_SHARED_NODES
        or informative <= GENERIC_CREDENTIAL_BUFFER_SHARED_NODES
        or informative <= GENERIC_MEMORY_SHARED_NODES
        or informative <= GENERIC_POPUP_SHARED_NODES
    )


@dataclass(frozen=True)
class HypergraphPairSkillRisk:
    skill_id: str
    name: str
    author_handle: str | None
    downloads: int
    stars: int
    as_source_count: int
    as_sink_count: int
    total_incident_count: int
    partner_span: int
    domain_span: int
    carrier_span: int
    max_risk_score: float
    mean_risk_score: float
    aggregate_risk_score: float


@dataclass(frozen=True)
class HypergraphPairAnalysisSummary:
    total_skills: int
    total_nodes: int
    total_hyperedges: int
    total_pairs: int
    source_candidates: int
    sink_candidates: int
    node_family_counts: dict[str, int]
    source_type_counts: dict[str, int]
    sink_type_counts: dict[str, int]
    carrier_counts: dict[str, int]
    channel_family_counts: dict[str, int]
    semantic_channel_counts: dict[str, int]
    system_channel_counts: dict[str, int]
    outcome_counts: dict[str, int]
    severity_counts: dict[str, int]
    schema_counts: dict[str, int]
    domain_cross_counts: dict[str, int]
    activation_order_mean: float
    activation_order_median: float


def _pair_coordination_ease(sink_tail_order: int) -> float:
    conjunctive_load = float(sink_tail_order)
    return max(0.20, min(1.0, 4.0 / max(conjunctive_load, 4.0)))


def _primary_shared_pair_carrier(
    source: HypergraphSkillProfile,
    sink: HypergraphSkillProfile,
    shared_nodes: tuple[str, ...],
) -> tuple[str, str]:
    channel_candidates = {
        node
        for node in shared_nodes
        if node.startswith("semantic_channel:") or node.startswith("system_channel:")
    }
    if not channel_candidates:
        channel_candidates = set(source.channel_nodes) & set(sink.channel_nodes)
    if not channel_candidates:
        channel_candidates = set(sink.channel_nodes) or set(source.channel_nodes)
    if not channel_candidates:
        return ("semantic_channel", "transform_buffer")
    selected = sorted(
        channel_candidates,
        key=lambda node: CHANNEL_BASE_PRIORS.get(node.split(":", 1)[1], 0.0),
        reverse=True,
    )[0]
    family, channel_name = selected.split(":", 1)
    return family, channel_name


def _pair_identity(pair: HypergraphRiskPair) -> tuple[str, str, str, str, str, str]:
    return (
        pair.category,
        pair.schema_name,
        pair.source_skill_id,
        pair.sink_skill_id,
        pair.source_type,
        f"{pair.channel_family}:{pair.carrier_type}",
    )


def _deduplicate_pairs(pairs: list[HypergraphRiskPair]) -> list[HypergraphRiskPair]:
    best_by_identity: dict[tuple[str, str, str, str, str, str], HypergraphRiskPair] = {}
    for pair in pairs:
        identity = _pair_identity(pair)
        current = best_by_identity.get(identity)
        if current is None:
            best_by_identity[identity] = pair
            continue
        current_rank = (
            current.risk_score,
            current.semantic_overlap,
            current.authority_score,
            len(current.shared_nodes),
        )
        candidate_rank = (
            pair.risk_score,
            pair.semantic_overlap,
            pair.authority_score,
            len(pair.shared_nodes),
        )
        if candidate_rank > current_rank:
            best_by_identity[identity] = pair
    return list(best_by_identity.values())


def _filter_risk_pairs(
    pairs: list[HypergraphRiskPair],
    *,
    min_risk_score: float | None = None,
    min_severity: str | None = None,
) -> list[HypergraphRiskPair]:
    if min_risk_score is None and min_severity is None:
        return pairs

    minimum_severity_rank = None
    if min_severity is not None:
        minimum_severity_rank = SEVERITY_RANKS[min_severity]

    filtered: list[HypergraphRiskPair] = []
    for pair in pairs:
        if min_risk_score is not None and pair.risk_score < min_risk_score:
            continue
        if minimum_severity_rank is not None:
            severity_rank = SEVERITY_RANKS.get(pair.severity_tier, -1)
            if severity_rank < minimum_severity_rank:
                continue
        filtered.append(pair)
    return filtered


def mine_hypergraph_risk_pairs(
    profiles: list[HypergraphSkillProfile],
    *,
    graph: DirectedHypergraph | None = None,
    top_k: int | None = 500,
    source_limit: int = 400,
    sink_limit: int = 80,
    min_risk_score: float | None = None,
    min_severity: str | None = None,
) -> list[HypergraphRiskPair]:
    if not profiles:
        return []
    graph = graph or build_directed_skill_hypergraph(profiles)
    sources, _, sinks = _candidate_profiles(profiles)
    sources = sorted(
        sources,
        key=lambda profile: (profile.downloads, profile.permission_flag_count, profile.stars),
        reverse=True,
    )[:source_limit]
    sink_ids = {profile.skill_id for profile in sinks}
    join_counts = Counter[str]()
    for profile in sinks:
        join_counts.update(profile.join_tail_nodes())
    allowed_join_nodes = {
        node
        for node, count in join_counts.items()
        if count <= MAX_JOIN_FANOUT
    }
    activation_index = _build_activation_index(sinks, allowed_nodes=allowed_join_nodes)
    max_downloads = math.log10(max(profile.downloads for profile in profiles) + 10.0)
    max_stars = math.log10(max(profile.stars for profile in profiles) + 10.0)
    carrier_prevalence = _carrier_prevalence(profiles)

    pairs: list[HypergraphRiskPair] = []
    for source in sources:
        sink_candidates: dict[str, tuple[float, tuple[str, ...]]] = {}
        for join_node in source.join_head_nodes():
            if join_node not in allowed_join_nodes:
                continue
            for sink_id in activation_index.get(join_node, ()):
                if sink_id == source.skill_id or sink_id not in sink_ids:
                    continue
                sink = graph.profiles[sink_id]
                shared_nodes = _unique_sorted(
                    set(source.emission_edge.head) & set(sink.activation_edge.tail)
                )
                if _is_generic_pair_overlap(shared_nodes):
                    continue
                overlap = _weighted_overlap(shared_nodes, sink.activation_edge.tail)
                if overlap <= 0.14:
                    continue
                pre_score = overlap + 0.25 * _sink_impact_prior(sink.primary_sink_type())
                current = sink_candidates.get(sink_id)
                if current is None or pre_score > current[0]:
                    sink_candidates[sink_id] = (pre_score, shared_nodes)
        if not sink_candidates:
            continue

        ordered_sinks = sorted(
            (
                (score, graph.profiles[sink_id], shared_nodes)
                for sink_id, (score, shared_nodes) in sink_candidates.items()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        for _, sink, shared_nodes in ordered_sinks[:sink_limit]:
            source_type = source.primary_source_type()
            sink_type = sink.primary_sink_type()
            source_sink_overlap = _weighted_overlap(shared_nodes, sink.activation_edge.tail)
            semantic_overlap = source_sink_overlap
            if semantic_overlap <= 0.18:
                continue

            channel_family, carrier_type = _primary_shared_pair_carrier(source, sink, shared_nodes)
            risk_outcome = _risk_outcome(sink, sink_type, channel_family, carrier_type)
            severity_tier = _severity_tier(risk_outcome, sink_type, carrier_type)
            authority_score = 0.40 * source.authority_surface() + 0.60 * sink.authority_surface()
            impact_score = _sink_impact_prior(sink_type)
            source_score = _source_risk_prior(source_type)
            exposure_score = _mean(
                (
                    source.exposure_score(max_downloads=max_downloads, max_stars=max_stars),
                    sink.exposure_score(max_downloads=max_downloads, max_stars=max_stars),
                )
            )
            audit_gap_score = _mean((source.audit_gap(), sink.audit_gap()))
            coordination_ease = _pair_coordination_ease(sink.activation_edge.order)
            carrier_score = _carrier_score(carrier_type, carrier_prevalence)
            base_score = (
                0.28 * semantic_overlap
                + 0.17 * carrier_score
                + 0.15 * impact_score
                + 0.13 * authority_score
                + 0.10 * source_score
                + 0.06 * exposure_score
                + 0.06 * audit_gap_score
                + 0.05 * coordination_ease
            )
            cross_domain = source.primary_domain() != sink.primary_domain()
            author_diversity = len(
                {
                    author_handle
                    for author_handle in (source.author_handle, sink.author_handle)
                    if author_handle
                }
            )
            risk_score = 100.0 * base_score
            risk_score *= 1.0 + (0.12 if cross_domain else 0.0) + 0.03 * max(author_diversity - 1, 0)
            risk_score = min(100.0, risk_score)
            rationale = ", ".join(
                [
                    f"source={source_type}",
                    f"sink={sink_type}",
                    f"outcome={risk_outcome}",
                    f"severity={severity_tier}",
                    f"channel={channel_family}:{carrier_type}",
                    f"shared={';'.join(shared_nodes[:6]) or 'none'}",
                ]
            )
            pairs.append(
                HypergraphRiskPair(
                    category=_combination_category(source_type, sink_type),
                    schema_name=_schema_name(sink_type),
                    risk_outcome=risk_outcome,
                    severity_tier=severity_tier,
                    source_type=source_type,
                    sink_type=sink_type,
                    source_skill_id=source.skill_id,
                    source_name=source.name,
                    source_domain=source.primary_domain(),
                    sink_skill_id=sink.skill_id,
                    sink_name=sink.name,
                    sink_domain=sink.primary_domain(),
                    channel_family=channel_family,
                    carrier_type=carrier_type,
                    cross_domain=cross_domain,
                    author_diversity=author_diversity,
                    source_sink_overlap=source_sink_overlap,
                    semantic_overlap=semantic_overlap,
                    coordination_ease=coordination_ease,
                    carrier_score=carrier_score,
                    authority_score=authority_score,
                    impact_score=impact_score,
                    source_score=source_score,
                    exposure_score=exposure_score,
                    audit_gap_score=audit_gap_score,
                    risk_score=risk_score,
                    shared_nodes=shared_nodes,
                    rationale=rationale,
                )
            )

    pairs = _deduplicate_pairs(pairs)
    pairs.sort(
        key=lambda pair: (pair.risk_score, pair.semantic_overlap, pair.authority_score),
        reverse=True,
    )
    pairs = _filter_risk_pairs(
        pairs,
        min_risk_score=min_risk_score,
        min_severity=min_severity,
    )
    if top_k is None:
        return pairs
    return pairs[:top_k]


def summarize_hypergraph_pair_skill_risks(
    profiles: list[HypergraphSkillProfile],
    pairs: list[HypergraphRiskPair],
    *,
    top_k: int | None = 50,
) -> list[HypergraphPairSkillRisk]:
    if not pairs:
        return []
    profile_map = {profile.skill_id: profile for profile in profiles}
    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "as_source_count": 0,
            "as_sink_count": 0,
            "risk_sum": 0.0,
            "risk_weighted_count": 0.0,
            "max_risk_score": 0.0,
            "partners": set(),
        }
    )

    for pair in pairs:
        for role, skill_id, partner_id, weight in (
            ("source", pair.source_skill_id, pair.sink_skill_id, 0.90),
            ("sink", pair.sink_skill_id, pair.source_skill_id, 1.00),
        ):
            record = stats[skill_id]
            record[f"as_{role}_count"] = int(record[f"as_{role}_count"]) + 1
            record["risk_sum"] = float(record["risk_sum"]) + pair.risk_score * weight
            record["risk_weighted_count"] = float(record["risk_weighted_count"]) + weight
            record["max_risk_score"] = max(float(record["max_risk_score"]), pair.risk_score)
            partners = record["partners"]
            assert isinstance(partners, set)
            partners.add(partner_id)

    risks: list[HypergraphPairSkillRisk] = []
    for skill_id, record in stats.items():
        profile = profile_map[skill_id]
        weighted_count = max(float(record["risk_weighted_count"]), 1.0)
        mean_risk_score = float(record["risk_sum"]) / weighted_count
        partner_span = len(record["partners"])
        aggregate_risk_score = (
            0.55 * float(record["max_risk_score"])
            + 0.25 * mean_risk_score
            + 20.0 * min(1.0, math.log1p(partner_span + int(record["as_sink_count"])) / 5.0)
        )
        risks.append(
            HypergraphPairSkillRisk(
                skill_id=skill_id,
                name=profile.name,
                author_handle=profile.author_handle,
                downloads=profile.downloads,
                stars=profile.stars,
                as_source_count=int(record["as_source_count"]),
                as_sink_count=int(record["as_sink_count"]),
                total_incident_count=int(record["as_source_count"]) + int(record["as_sink_count"]),
                partner_span=partner_span,
                domain_span=len(profile.domain_nodes),
                carrier_span=len(profile.carrier_nodes),
                max_risk_score=float(record["max_risk_score"]),
                mean_risk_score=mean_risk_score,
                aggregate_risk_score=min(100.0, aggregate_risk_score),
            )
        )
    risks.sort(
        key=lambda risk: (risk.aggregate_risk_score, risk.partner_span, risk.max_risk_score),
        reverse=True,
    )
    if top_k is None:
        return risks
    return risks[:top_k]


def summarize_hypergraph_pair_analysis(
    graph: DirectedHypergraph,
    pairs: list[HypergraphRiskPair],
) -> HypergraphPairAnalysisSummary:
    node_family_counts = Counter(node.split(":", 1)[0] for node in graph.nodes)
    source_type_counts = Counter(pair.source_type for pair in pairs)
    sink_type_counts = Counter(pair.sink_type for pair in pairs)
    carrier_counts = Counter(pair.carrier_type for pair in pairs)
    channel_family_counts = Counter(pair.channel_family for pair in pairs)
    semantic_channel_counts = Counter(
        node.split(":", 1)[1]
        for profile in graph.profiles.values()
        for node in profile.semantic_channel_nodes
    )
    system_channel_counts = Counter(
        node.split(":", 1)[1]
        for profile in graph.profiles.values()
        for node in profile.system_channel_nodes
    )
    outcome_counts = Counter(pair.risk_outcome for pair in pairs)
    severity_counts = Counter(pair.severity_tier for pair in pairs)
    schema_counts = Counter(pair.schema_name for pair in pairs)
    domain_cross_counts = Counter(f"{pair.source_domain}->{pair.sink_domain}" for pair in pairs)
    orders = [edge.order for edge in graph.activation_edges]
    sorted_orders = sorted(orders)
    if sorted_orders:
        middle = len(sorted_orders) // 2
        if len(sorted_orders) % 2 == 0:
            median_order = (sorted_orders[middle - 1] + sorted_orders[middle]) / 2.0
        else:
            median_order = float(sorted_orders[middle])
    else:
        median_order = 0.0
    source_candidates, _, sink_candidates = _candidate_profiles(list(graph.profiles.values()))
    return HypergraphPairAnalysisSummary(
        total_skills=len(graph.profiles),
        total_nodes=len(graph.nodes),
        total_hyperedges=graph.edge_count,
        total_pairs=len(pairs),
        source_candidates=len(source_candidates),
        sink_candidates=len(sink_candidates),
        node_family_counts=dict(node_family_counts),
        source_type_counts=dict(source_type_counts),
        sink_type_counts=dict(sink_type_counts),
        carrier_counts=dict(carrier_counts),
        channel_family_counts=dict(channel_family_counts),
        semantic_channel_counts=dict(semantic_channel_counts),
        system_channel_counts=dict(system_channel_counts),
        outcome_counts=dict(outcome_counts),
        severity_counts=dict(severity_counts),
        schema_counts=dict(schema_counts),
        domain_cross_counts=dict(domain_cross_counts),
        activation_order_mean=_mean(orders),
        activation_order_median=median_order,
    )


def write_hypergraph_risk_pairs_csv(path: Path, pairs: list[HypergraphRiskPair]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category",
                "schema_name",
                "risk_outcome",
                "severity_tier",
                "source_type",
                "sink_type",
                "source_skill_id",
                "source_name",
                "source_domain",
                "sink_skill_id",
                "sink_name",
                "sink_domain",
                "channel_family",
                "carrier_type",
                "cross_domain",
                "author_diversity",
                "source_sink_overlap",
                "semantic_overlap",
                "coordination_ease",
                "carrier_score",
                "authority_score",
                "impact_score",
                "source_score",
                "exposure_score",
                "audit_gap_score",
                "risk_score",
                "shared_nodes",
                "rationale",
            ],
        )
        writer.writeheader()
        for pair in pairs:
            writer.writerow(
                {
                    "category": pair.category,
                    "schema_name": pair.schema_name,
                    "risk_outcome": pair.risk_outcome,
                    "severity_tier": pair.severity_tier,
                    "source_type": pair.source_type,
                    "sink_type": pair.sink_type,
                    "source_skill_id": pair.source_skill_id,
                    "source_name": pair.source_name,
                    "source_domain": pair.source_domain,
                    "sink_skill_id": pair.sink_skill_id,
                    "sink_name": pair.sink_name,
                    "sink_domain": pair.sink_domain,
                    "channel_family": pair.channel_family,
                    "carrier_type": pair.carrier_type,
                    "cross_domain": int(pair.cross_domain),
                    "author_diversity": pair.author_diversity,
                    "source_sink_overlap": f"{pair.source_sink_overlap:.6f}",
                    "semantic_overlap": f"{pair.semantic_overlap:.6f}",
                    "coordination_ease": f"{pair.coordination_ease:.6f}",
                    "carrier_score": f"{pair.carrier_score:.6f}",
                    "authority_score": f"{pair.authority_score:.6f}",
                    "impact_score": f"{pair.impact_score:.6f}",
                    "source_score": f"{pair.source_score:.6f}",
                    "exposure_score": f"{pair.exposure_score:.6f}",
                    "audit_gap_score": f"{pair.audit_gap_score:.6f}",
                    "risk_score": f"{pair.risk_score:.6f}",
                    "shared_nodes": "|".join(pair.shared_nodes),
                    "rationale": pair.rationale,
                }
            )


def write_hypergraph_pair_skill_risks_csv(path: Path, risks: list[HypergraphPairSkillRisk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "skill_id",
                "name",
                "author_handle",
                "downloads",
                "stars",
                "as_source_count",
                "as_sink_count",
                "total_incident_count",
                "partner_span",
                "domain_span",
                "carrier_span",
                "max_risk_score",
                "mean_risk_score",
                "aggregate_risk_score",
            ],
        )
        writer.writeheader()
        for risk in risks:
            writer.writerow(
                {
                    "skill_id": risk.skill_id,
                    "name": risk.name,
                    "author_handle": risk.author_handle or "",
                    "downloads": risk.downloads,
                    "stars": risk.stars,
                    "as_source_count": risk.as_source_count,
                    "as_sink_count": risk.as_sink_count,
                    "total_incident_count": risk.total_incident_count,
                    "partner_span": risk.partner_span,
                    "domain_span": risk.domain_span,
                    "carrier_span": risk.carrier_span,
                    "max_risk_score": f"{risk.max_risk_score:.6f}",
                    "mean_risk_score": f"{risk.mean_risk_score:.6f}",
                    "aggregate_risk_score": f"{risk.aggregate_risk_score:.6f}",
                }
            )


def _render_pair_metric_table() -> list[str]:
    return [
        "| Metric | Definition | Dataset-backed indicator |",
        "| --- | --- | --- |",
        "| `source_sink_overlap` | Weighted head-tail overlap between source emission and sink activation. | Shared `object/channel` nodes induced from public skill metadata. |",
        "| `semantic_overlap` | Pairwise static reachability prior, equal to `source_sink_overlap` in the 2-skill setting. | Hyperedge compatibility strength in the directed hypergraph. |",
        "| `coordination_ease` | Penalty for large sink activation tails. | Activation hyperedge order from observed precondition nodes. |",
        "| `carrier_score` | Persistence and reuse strength of the dominant semantic/system channel. | Channel type plus observed ecosystem prevalence. |",
        "| `authority_score` | Privilege surface needed by the pair. | Public permission hints (`requires_*`). |",
        "| `impact_score` | Severity of the terminal effect. | Stage 2 sink taxonomy (`exfil/publish/modify/execute/control/delete/transact`). |",
        "| `exposure_score` | Ecosystem exposure if a pair exists. | `downloads` and `stars`. |",
        "| `audit_gap_score` | Lower public assurance increases uncertainty and risk. | `scan_status`, `public_version_count`, `install_command`. |",
    ]


def write_hypergraph_pair_report(
    path: Path,
    summary: HypergraphPairAnalysisSummary,
    pairs: list[HypergraphRiskPair],
    skill_risks: list[HypergraphPairSkillRisk],
    channel_inventory: list[HypergraphChannelInventory],
    *,
    source_limit: int,
    sink_limit: int,
    min_risk_score: float | None = None,
    min_severity: str | None = None,
    report_top_k: int = 20,
    skill_top_k: int = 20,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    semantic_inventory = [row for row in channel_inventory if row.channel_family == "semantic_channel"]
    system_inventory = [row for row in channel_inventory if row.channel_family == "system_channel"]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Stage 2 Channel-Centric Hypergraph Pair Risk Analysis\n\n")
        handle.write("## Model\n\n")
        handle.write(
            "This v3 variant keeps the channel-centric Stage 2 representation from the earlier reports, but "
            "collapses the search space to direct 2-skill compositions. Each skill is still modeled as a "
            "directed hypergraph fragment with an `activation` edge from public precondition nodes to the "
            "skill node, and an `emission` edge from the skill node to output/state/effect nodes.\n\n"
        )
        handle.write("For skill `s`, the fragment is:\n\n")
        handle.write("```text\n")
        handle.write("T_activate(s) -> skill:s -> H_emit(s)\n")
        handle.write("```\n\n")
        handle.write("A static high-risk pair is a 2-skill composition `(source, sink)` where:\n\n")
        handle.write("1. `H_emit(source) ∩ T_activate(sink) != ∅`\n")
        handle.write("2. the source carries a Stage 2 source type\n")
        handle.write("3. the sink exposes a high-impact sink type\n\n")
        handle.write(
            "As in v2, `semantic_channel` nodes denote reusable state/content carriers such as memory, "
            "workspace, and transform buffers, while `system_channel` nodes denote runtime surfaces such as "
            "credential state, browser state, keyboard/mouse I/O, screen/audio/video capture, desktop state, "
            "and device signals. The result is still a candidate-discovery layer rather than a runtime proof "
            "of exploitability.\n\n"
        )
        handle.write(
            "The final risk score is a weighted linear risk functional over structural overlap, channel "
            "persistence, privilege surface, sink impact, source prior, exposure, audit gap, and sink-side "
            "coordination ease, followed by small bonuses for cross-domain and cross-author pairs.\n\n"
        )
        handle.write("## Metric Definitions\n\n")
        for line in _render_pair_metric_table():
            handle.write(f"{line}\n")

        handle.write("\n## Search Configuration\n\n")
        handle.write(f"- source limit: `{source_limit}`\n")
        handle.write(f"- sink candidates kept per source: `{sink_limit}`\n")
        handle.write(
            f"- minimum risk score: `{min_risk_score:.2f}`\n"
            if min_risk_score is not None
            else "- minimum risk score: `none`\n"
        )
        handle.write(
            f"- minimum severity: `{min_severity}`\n"
            if min_severity is not None
            else "- minimum severity: `none`\n"
        )
        handle.write(f"- total pairs mined: `{summary.total_pairs}`\n\n")

        handle.write("## Graph Size\n\n")
        handle.write(f"- skills: `{summary.total_skills}`\n")
        handle.write(f"- hypernodes: `{summary.total_nodes}`\n")
        handle.write(f"- hyperedges: `{summary.total_hyperedges}`\n")
        handle.write(f"- source-capable skills: `{summary.source_candidates}`\n")
        handle.write(f"- sink-capable skills: `{summary.sink_candidates}`\n")
        handle.write(f"- mean activation order: `{summary.activation_order_mean:.2f}`\n")
        handle.write(f"- median activation order: `{summary.activation_order_median:.2f}`\n\n")

        handle.write("## Node Families\n\n")
        handle.write("| Family | Count |\n")
        handle.write("| --- | ---: |\n")
        for family, count in sorted(summary.node_family_counts.items()):
            handle.write(f"| {family} | {count} |\n")

        total_pairs = max(summary.total_pairs, 1)
        handle.write("\n## Schema Distribution\n\n")
        handle.write("| Schema | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for schema_name, count in sorted(summary.schema_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {schema_name} | {count} | {count / total_pairs:.2%} |\n")

        handle.write("\n## OpenClaw System Channels\n\n")
        handle.write("| System Channel | Subtype | Skills | Source-capable | Bridge-capable | Sink-capable | Example Skills |\n")
        handle.write("| --- | --- | ---: | ---: | ---: | ---: | --- |\n")
        for row in sorted(system_inventory, key=lambda item: item.skill_count, reverse=True):
            examples = ", ".join(f"`{skill_id}`" for skill_id in row.example_skill_ids[:3])
            handle.write(
                f"| {row.channel_name} | {row.channel_subtype} | {row.skill_count} | {row.source_skill_count} | "
                f"{row.bridge_skill_count} | {row.sink_skill_count} | {examples or '-'} |\n"
            )

        handle.write("\n## Semantic Channels\n\n")
        handle.write("| Semantic Channel | Subtype | Skills | Source-capable | Bridge-capable | Sink-capable | Example Skills |\n")
        handle.write("| --- | --- | ---: | ---: | ---: | ---: | --- |\n")
        for row in sorted(semantic_inventory, key=lambda item: item.skill_count, reverse=True):
            examples = ", ".join(f"`{skill_id}`" for skill_id in row.example_skill_ids[:3])
            handle.write(
                f"| {row.channel_name} | {row.channel_subtype} | {row.skill_count} | {row.source_skill_count} | "
                f"{row.bridge_skill_count} | {row.sink_skill_count} | {examples or '-'} |\n"
            )

        handle.write("\n## Risk Outcomes\n\n")
        handle.write("| Outcome | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for outcome, count in sorted(summary.outcome_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {outcome} | {count} | {count / total_pairs:.2%} |\n")

        handle.write("\n## Severity Distribution\n\n")
        handle.write("| Severity | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for severity, count in sorted(summary.severity_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {severity} | {count} | {count / total_pairs:.2%} |\n")

        handle.write("\n## Risky Channel Distribution\n\n")
        handle.write("| Channel | Family | Count | Ratio |\n")
        handle.write("| --- | --- | ---: | ---: |\n")
        risky_channel_family = {
            pair.carrier_type: pair.channel_family.removesuffix("_channel")
            for pair in pairs
        }
        for carrier_name, count in sorted(summary.carrier_counts.items(), key=lambda item: item[1], reverse=True):
            family = risky_channel_family.get(carrier_name, "unknown")
            handle.write(f"| {carrier_name} | {family} | {count} | {count / total_pairs:.2%} |\n")

        handle.write("\n## Trust Domain Crossings\n\n")
        handle.write("| Crossing | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for crossing, count in sorted(summary.domain_cross_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {crossing} | {count} | {count / total_pairs:.2%} |\n")

        handle.write("\n## Top Risky Skills\n\n")
        handle.write("| Skill | Role Counts (S/T) | Partner Span | Max Risk | Mean Risk | Aggregate |\n")
        handle.write("| --- | ---: | ---: | ---: | ---: | ---: |\n")
        for risk in skill_risks[:skill_top_k]:
            role_counts = f"{risk.as_source_count}/{risk.as_sink_count}"
            handle.write(
                f"| `{risk.skill_id}` | {role_counts} | {risk.partner_span} | "
                f"{risk.max_risk_score:.2f} | {risk.mean_risk_score:.2f} | {risk.aggregate_risk_score:.2f} |\n"
            )

        handle.write("\n## Top Hypergraph Pairs\n\n")
        handle.write(
            "| Category | Outcome | Severity | Source | Sink | Channel | Family | Overlap | Authority | Impact | Risk |\n"
        )
        handle.write("| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |\n")
        for pair in pairs[:report_top_k]:
            handle.write(
                f"| {pair.category} | {pair.risk_outcome} | {pair.severity_tier} | `{pair.source_skill_id}` | "
                f"`{pair.sink_skill_id}` | {pair.carrier_type} | {pair.channel_family} | {pair.semantic_overlap:.3f} | "
                f"{pair.authority_score:.3f} | {pair.impact_score:.3f} | {pair.risk_score:.2f} |\n"
            )


def generate_hypergraph_pair_risk_artifacts(
    *,
    db_path: Path,
    output_dir: Path,
    source_limit: int = 400,
    sink_limit: int = 80,
    csv_top_k: int = 500,
    min_risk_score: float | None = None,
    min_severity: str | None = None,
    report_top_k: int = 20,
    skill_top_k: int = 50,
) -> dict[str, Path]:
    print(f"pair_analysis: loading profiles from {db_path}", flush=True)
    profiles = load_hypergraph_skill_profiles(db_path, progress_label="pair_analysis")
    print(f"pair_analysis: loaded_profiles={len(profiles)}", flush=True)
    print("pair_analysis: building hypergraph", flush=True)
    graph = build_directed_skill_hypergraph(profiles)
    print(
        f"pair_analysis: graph_nodes={len(graph.nodes)} activation_edges={len(graph.activation_edges)} emission_edges={len(graph.emission_edges)}",
        flush=True,
    )
    print(
        "pair_analysis: mining pairs "
        f"source_limit={source_limit} sink_limit={sink_limit} top_k={csv_top_k} "
        f"min_risk_score={min_risk_score if min_risk_score is not None else 'none'} "
        f"min_severity={min_severity or 'none'}",
        flush=True,
    )
    pairs = mine_hypergraph_risk_pairs(
        profiles,
        graph=graph,
        top_k=csv_top_k,
        source_limit=source_limit,
        sink_limit=sink_limit,
        min_risk_score=min_risk_score,
        min_severity=min_severity,
    )
    print(f"pair_analysis: mined_pairs={len(pairs)}", flush=True)
    print("pair_analysis: summarizing skills and channels", flush=True)
    skill_risks = summarize_hypergraph_pair_skill_risks(profiles, pairs, top_k=skill_top_k)
    channel_inventory = summarize_hypergraph_channel_inventory(profiles)
    summary = summarize_hypergraph_pair_analysis(graph, pairs)

    output_dir.mkdir(parents=True, exist_ok=True)
    pairs_path = output_dir / "hypergraph_risk_pairs.csv"
    skills_path = output_dir / "hypergraph_risky_skills.csv"
    channels_path = output_dir / "hypergraph_channel_inventory.csv"
    report_path = output_dir / "hypergraph_risk_report.md"

    print(f"pair_analysis: writing outputs under {output_dir}", flush=True)
    write_hypergraph_risk_pairs_csv(pairs_path, pairs)
    write_hypergraph_pair_skill_risks_csv(skills_path, skill_risks)
    write_hypergraph_channel_inventory_csv(channels_path, channel_inventory)
    write_hypergraph_pair_report(
        report_path,
        summary,
        pairs,
        skill_risks,
        channel_inventory,
        source_limit=source_limit,
        sink_limit=sink_limit,
        min_risk_score=min_risk_score,
        min_severity=min_severity,
        report_top_k=report_top_k,
        skill_top_k=skill_top_k,
    )
    print("pair_analysis: done", flush=True)
    return {
        "pairs_csv": pairs_path,
        "skills_csv": skills_path,
        "channel_inventory_csv": channels_path,
        "report_markdown": report_path,
    }
