from pathlib import Path
import sqlite3

from clawhub_analyzer.cape.types import Capability
from clawhub_analyzer.hypergraph import (
    build_directed_skill_hypergraph,
    derive_hypergraph_profile,
    mine_hypergraph_risk_combinations,
    summarize_hypergraph_channel_inventory,
    summarize_hypergraph_skill_risks,
)
from clawhub_analyzer.hypergraph_pairs import (
    mine_hypergraph_risk_pairs,
    summarize_hypergraph_pair_skill_risks,
)


def make_row(**overrides: object) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE t (
            skill_id TEXT,
            author_handle TEXT,
            name TEXT,
            downloads INTEGER,
            stars INTEGER,
            installs_all_time INTEGER,
            description TEXT,
            tags TEXT,
            metadata_systems TEXT,
            install_command TEXT,
            scan_status TEXT,
            public_version_count INTEGER,
            requires_api_key INTEGER,
            requires_oauth INTEGER,
            requires_token INTEGER,
            requires_local_file_access INTEGER,
            requires_shell_access INTEGER,
            requires_browser_access INTEGER,
            requires_github INTEGER,
            requires_slack INTEGER,
            requires_notion INTEGER,
            requires_calendar INTEGER,
            requires_email INTEGER,
            requires_docs INTEGER,
            requires_pdf INTEGER,
            raw_evidence TEXT
        )
        """
    )
    base = {
        "skill_id": "skill/a",
        "author_handle": "alice",
        "name": "Workspace Memory Helper",
        "downloads": 100,
        "stars": 10,
        "installs_all_time": 80,
        "description": "Stores browser notes into the workspace and memory for later sync.",
        "tags": "",
        "metadata_systems": "",
        "install_command": "npx helper",
        "scan_status": "",
        "public_version_count": 1,
        "requires_api_key": 0,
        "requires_oauth": 0,
        "requires_token": 0,
        "requires_local_file_access": 1,
        "requires_shell_access": 0,
        "requires_browser_access": 0,
        "requires_github": 0,
        "requires_slack": 0,
        "requires_notion": 0,
        "requires_calendar": 0,
        "requires_email": 0,
        "requires_docs": 1,
        "requires_pdf": 0,
        "raw_evidence": "[]",
    }
    base.update(overrides)
    columns = ", ".join(base)
    placeholders = ", ".join("?" for _ in base)
    connection.execute(
        f"INSERT INTO t ({columns}) VALUES ({placeholders})",
        list(base.values()),
    )
    row = connection.execute("SELECT * FROM t").fetchone()
    assert row is not None
    return row


def test_derive_hypergraph_profile_builds_activation_and_emission_edges() -> None:
    profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    assert profile.activation_edge.edge_kind == "activation"
    assert "source:untrusted_input" in profile.activation_edge.tail
    assert "object:raw_content" in profile.emission_edge.head
    assert "system_channel:browser_state" in profile.emission_edge.head


def test_derive_hypergraph_profile_uses_file_capability_channel_direction() -> None:
    writer = derive_hypergraph_profile(
        make_row(
            skill_id="skill/writer",
            name="Plain Writer",
            description="A plain helper.",
            requires_local_file_access=0,
            requires_docs=0,
        ),
        [
            Capability("source", "web_fetch", "src/main.py:L1:requests.get"),
            Capability("channel", "workspace_file", "src/main.py:L9:open-workspace-write", role="write"),
        ],
    )
    reader = derive_hypergraph_profile(
        make_row(
            skill_id="skill/reader",
            author_handle="bob",
            name="Plain Reader",
            description="A plain helper.",
            requires_local_file_access=0,
            requires_docs=0,
        ),
        [
            Capability("channel", "workspace_file", "src/main.py:L4:open-workspace-read", role="read"),
            Capability("sink", "network_send", "src/main.py:L8:requests.post"),
        ],
    )

    assert "semantic_channel:workspace" in writer.emission_channel_nodes()
    assert "semantic_channel:workspace" not in writer.activation_channel_nodes()
    assert "semantic_channel:workspace" in reader.activation_channel_nodes()
    assert "semantic_channel:workspace" not in reader.emission_channel_nodes()
    assert writer.file_capability_count == 2
    assert reader.file_capability_count == 2


def test_mine_hypergraph_risk_combinations_finds_workspace_chain() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    bridge = derive_hypergraph_profile(
        make_row(
            skill_id="skill/summary",
            author_handle="bob",
            name="Workspace Summarizer",
            description="Summarize browser content into workspace notes and memory for later use.",
            downloads=1600,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/slack",
            author_handle="carol",
            name="Slack API Gateway",
            description="Read workspace notes and send them to Slack via API.",
            downloads=1800,
            requires_api_key=1,
            requires_slack=1,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )

    combinations = mine_hypergraph_risk_combinations(
        [source, bridge, sink],
        top_k=None,
        source_limit=10,
        bridge_limit=10,
        sink_limit=10,
    )

    assert combinations
    target = next(
        (
            combination
            for combination in combinations
            if combination.source_skill_id == "skill/browser"
            and combination.bridge_skill_id == "skill/summary"
            and combination.sink_skill_id == "skill/slack"
        ),
        None,
    )
    assert target is not None
    assert target.channel_family == "semantic_channel"
    assert target.risk_outcome in {"information_leakage", "harmful_operation", "availability_disruption"}
    assert target.severity_tier in {"low", "medium", "high", "critical"}
    assert target.carrier_type in {"workspace", "memory", "transform_buffer"}
    assert target.risk_score > 0


def test_mine_hypergraph_risk_combinations_deduplicates_logical_triplets() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    bridge = derive_hypergraph_profile(
        make_row(
            skill_id="skill/summary",
            author_handle="bob",
            name="Workspace Summarizer",
            description="Summarize browser content into workspace notes and memory for later use.",
            downloads=1600,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/slack",
            author_handle="carol",
            name="Slack API Gateway",
            description="Read workspace notes and send them to Slack via API.",
            downloads=1800,
            requires_api_key=1,
            requires_slack=1,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )

    combinations = mine_hypergraph_risk_combinations(
        [source, source, bridge, sink],
        top_k=None,
        source_limit=10,
        bridge_limit=10,
        sink_limit=10,
    )

    identities = [
        (
            combination.category,
            combination.schema_name,
            combination.source_skill_id,
            combination.bridge_skill_id,
            combination.sink_skill_id,
            combination.channel_family,
            combination.carrier_type,
        )
        for combination in combinations
    ]
    assert len(identities) == len(set(identities))


def test_summarize_hypergraph_skill_risks_prefers_bridge_skill() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    bridge = derive_hypergraph_profile(
        make_row(
            skill_id="skill/summary",
            author_handle="bob",
            name="Workspace Summarizer",
            description="Summarize browser content into workspace notes and memory for later use.",
            downloads=1600,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/slack",
            author_handle="carol",
            name="Slack API Gateway",
            description="Read workspace notes and send them to Slack via API.",
            downloads=1800,
            requires_api_key=1,
            requires_slack=1,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )

    combinations = mine_hypergraph_risk_combinations(
        [source, bridge, sink],
        top_k=None,
        source_limit=10,
        bridge_limit=10,
        sink_limit=10,
    )
    risks = summarize_hypergraph_skill_risks([source, bridge, sink], combinations, top_k=None)

    assert risks
    bridge_risk = next((risk for risk in risks if risk.skill_id == "skill/summary"), None)
    assert bridge_risk is not None
    assert bridge_risk.as_bridge_count >= 1
    assert bridge_risk.bridge_brokerage >= 1


def test_build_directed_skill_hypergraph_counts_nodes_and_edges() -> None:
    profile = derive_hypergraph_profile(make_row())
    graph = build_directed_skill_hypergraph([profile])

    assert len(graph.activation_edges) == 1
    assert len(graph.emission_edges) == 1
    assert f"skill:{profile.skill_id}" in graph.nodes


def test_channel_inventory_reports_time_as_system_channel() -> None:
    calendar_profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/calendar",
            name="Calendar Scheduler",
            description="Schedule recurring calendar events and reminders.",
            requires_calendar=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    inventory = summarize_hypergraph_channel_inventory([calendar_profile])
    time_row = next(
        (row for row in inventory if row.channel_family == "system_channel" and row.channel_name == "time"),
        None,
    )
    assert time_row is not None
    assert time_row.skill_count == 1
    assert time_row.channel_subtype == "runtime_state"


def test_mine_hypergraph_risk_pairs_finds_direct_browser_to_publish_pair() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/export",
            author_handle="bob",
            name="Browser Slack Exporter",
            description="Read browser content and send it to Slack via API.",
            downloads=1800,
            requires_api_key=1,
            requires_slack=1,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    pairs = mine_hypergraph_risk_pairs(
        [source, sink],
        top_k=None,
        source_limit=10,
        sink_limit=10,
    )

    assert pairs
    target = next(
        (
            pair
            for pair in pairs
            if pair.source_skill_id == "skill/browser" and pair.sink_skill_id == "skill/export"
        ),
        None,
    )
    assert target is not None
    assert target.semantic_overlap > 0
    assert target.channel_family in {"semantic_channel", "system_channel"}
    assert target.risk_outcome in {"information_leakage", "harmful_operation", "availability_disruption"}


def test_summarize_hypergraph_pair_skill_risks_counts_sink_role() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/export",
            author_handle="bob",
            name="Browser Slack Exporter",
            description="Read browser content and send it to Slack via API.",
            downloads=1800,
            requires_api_key=1,
            requires_slack=1,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    pairs = mine_hypergraph_risk_pairs(
        [source, sink],
        top_k=None,
        source_limit=10,
        sink_limit=10,
    )
    risks = summarize_hypergraph_pair_skill_risks([source, sink], pairs, top_k=None)

    assert risks
    sink_risk = next((risk for risk in risks if risk.skill_id == "skill/export"), None)
    assert sink_risk is not None
    assert sink_risk.as_sink_count >= 1
    assert sink_risk.partner_span >= 1


def test_derive_hypergraph_profile_avoids_clickthrough_mouse_false_positive() -> None:
    profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/ctr",
            name="CTR Booster",
            description="Maximize CTR (Click-Through Rate) by improving titles and captions.",
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    assert "system_channel:mouse_io" not in profile.system_channel_nodes
    assert "sink:control" not in profile.sink_nodes


def test_derive_hypergraph_profile_avoids_remove_and_voice_false_positives() -> None:
    profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/de-ai-ify",
            name="De-AI-ify",
            description="Remove AI-generated jargon and restore human voice to text.",
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    assert "system_channel:audio_capture" not in profile.system_channel_nodes
    assert "sink:delete" not in profile.sink_nodes


def test_derive_hypergraph_profile_preserves_real_transact_without_notebook_false_positive() -> None:
    notebook = derive_hypergraph_profile(
        make_row(
            skill_id="skill/notebook",
            name="Notebook",
            description="Local-first personal knowledge base for tracking ideas, projects, and tasks.",
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    booker = derive_hypergraph_profile(
        make_row(
            skill_id="skill/book",
            name="Book Videographer",
            description="Book videographer services when the user needs to find and book a videographer.",
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    memory_tiering = derive_hypergraph_profile(
        make_row(
            skill_id="skill/memory",
            name="Memory Tiering",
            description="Automated memory management with pruning and archive compactions.",
            requires_local_file_access=1,
            requires_docs=1,
        )
    )

    assert "sink:transact" not in notebook.sink_nodes
    assert "sink:transact" in booker.sink_nodes
    assert "sink:publish" not in memory_tiering.sink_nodes


def test_derive_hypergraph_profile_requires_explicit_control_surface() -> None:
    profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/telegram-inline",
            name="Telegram Inline Buttons",
            description="Use inline buttons on Telegram to reduce typing and force crisp decisions.",
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    assert "sink:control" not in profile.sink_nodes
    assert "sink:execute" not in profile.sink_nodes


def test_mine_hypergraph_risk_pairs_skips_transform_buffer_only_pairs() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/source",
            name="Web Planner",
            description="Browse the web and analyze findings into concise plans.",
            downloads=2000,
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/sink",
            author_handle="bob",
            name="Shell Planner",
            description="Execute plans via shell commands.",
            downloads=1800,
            requires_shell_access=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    pairs = mine_hypergraph_risk_pairs(
        [source, sink],
        top_k=None,
        source_limit=10,
        sink_limit=10,
    )

    assert not any(
        pair.source_skill_id == "skill/source" and pair.sink_skill_id == "skill/sink"
        for pair in pairs
    )


def test_mine_hypergraph_risk_pairs_skips_generic_credential_schedule_pairs() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/source-saas",
            name="Calendar API Gateway",
            description="Connect to SaaS calendars and read schedules with managed OAuth.",
            downloads=2000,
            requires_api_key=1,
            requires_calendar=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/sink-saas",
            author_handle="bob",
            name="Twitter Operations",
            description="Automate and manage Twitter posting and scheduling.",
            downloads=1800,
            requires_api_key=1,
            requires_calendar=1,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    pairs = mine_hypergraph_risk_pairs(
        [source, sink],
        top_k=None,
        source_limit=10,
        sink_limit=10,
    )

    assert not any(
        pair.source_skill_id == "skill/source-saas" and pair.sink_skill_id == "skill/sink-saas"
        for pair in pairs
    )


def test_mine_hypergraph_risk_pairs_skips_generic_memory_pairs() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/source-memory",
            name="Memory OS",
            description="Persistent memory and notes for recall across sessions.",
            downloads=2000,
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/sink-memory",
            author_handle="bob",
            name="System Info",
            description="Quick system diagnostics: CPU, memory, disk, uptime.",
            downloads=1800,
            requires_local_file_access=0,
            requires_docs=0,
        )
    )

    pairs = mine_hypergraph_risk_pairs(
        [source, sink],
        top_k=None,
        source_limit=10,
        sink_limit=10,
    )

    assert not any(
        pair.source_skill_id == "skill/source-memory" and pair.sink_skill_id == "skill/sink-memory"
        for pair in pairs
    )
