from pathlib import Path
import sqlite3

from clawhub_analyzer.toxicity import (
    derive_capabilities,
    mine_lsagm_attack_paths,
    randomized_typed_pair_baseline,
    rank_covert_pairs,
    sink_types,
    source_types,
    system_channel_types,
    trust_domains,
    typed_pair_compatible,
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
            description TEXT,
            tags TEXT,
            metadata_systems TEXT,
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
            requires_pdf INTEGER
        )
        """
    )
    base = {
        "skill_id": "skill/a",
        "author_handle": "alice",
        "name": "Memory Keeper",
        "downloads": 100,
        "stars": 1,
        "description": "Stores memory notes from email and docs into the workspace.",
        "tags": "",
        "metadata_systems": "",
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
        "requires_email": 1,
        "requires_docs": 1,
        "requires_pdf": 0,
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


def test_derive_capabilities_marks_memory_and_workspace_channels() -> None:
    caps = derive_capabilities(make_row())
    assert caps.memory_channel is True
    assert caps.workspace_channel is True
    assert caps.sensitive_read is True


def test_calendar_skill_promotes_time_to_system_channel() -> None:
    caps = derive_capabilities(
        make_row(
            skill_id="skill/calendar",
            name="Calendar Scheduler",
            description="Schedule calendar events, reminders, and weekly follow-ups.",
            requires_calendar=1,
            requires_local_file_access=0,
            requires_docs=0,
            requires_email=0,
        )
    )
    assert caps.time_channel is True
    assert "time" in system_channel_types(caps)


def test_browser_and_notification_are_system_channels() -> None:
    caps = derive_capabilities(
        make_row(
            skill_id="skill/browser_notify",
            name="Browser Notifier",
            description="Read browser tabs and send popup notifications with window focus changes.",
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
            requires_email=0,
        )
    )
    channels = set(system_channel_types(caps))
    assert "browser_state" in channels
    assert "notification_signal" in channels
    assert "window_focus" in channels


def test_rank_covert_pairs_prefers_memory_to_exfil_path() -> None:
    source = derive_capabilities(make_row(skill_id="skill/source", downloads=500))
    sink = derive_capabilities(
        make_row(
            skill_id="skill/sink",
            author_handle="bob",
            name="Slack Sync",
            downloads=400,
            description="Reads workspace notes and syncs them to Slack via API.",
            requires_api_key=1,
            requires_slack=1,
            requires_local_file_access=1,
        )
    )
    candidates = rank_covert_pairs([source, sink], top_k=10, source_limit=10, sink_limit=10)
    assert candidates
    assert candidates[0].medium in {"memory", "workspace"}
    assert candidates[0].source_skill_id == "skill/source"
    assert candidates[0].sink_skill_id == "skill/sink"


def test_typed_pair_compatible_accepts_shared_domain_pairs() -> None:
    source = derive_capabilities(
        make_row(
            skill_id="skill/browser",
            name="Github Search",
            description="Search GitHub issues and web content.",
            requires_browser_access=1,
            requires_github=1,
        )
    )
    sink = derive_capabilities(
        make_row(
            skill_id="skill/action",
            author_handle="bob",
            name="Github Writer",
            description="Write updates back to GitHub via API.",
            requires_api_key=1,
            requires_github=1,
            requires_local_file_access=0,
        )
    )
    compatible, rationale = typed_pair_compatible(source, sink)
    assert compatible is True
    assert "github" in rationale or "cross_surface" in rationale


def test_randomized_typed_pair_baseline_returns_expected_shape() -> None:
    source = derive_capabilities(make_row(skill_id="skill/source", name="Docs Reader"))
    sink = derive_capabilities(
        make_row(
            skill_id="skill/sink",
            author_handle="bob",
            name="Slack Writer",
            description="Send docs to Slack over API.",
            requires_api_key=1,
            requires_slack=1,
        )
    )
    baseline = randomized_typed_pair_baseline([source, sink], iterations=3, seed=1)
    assert len(baseline) == 3
    assert set(baseline[0]) == {
        "overt_input_to_action",
        "overt_read_to_exfil",
        "overt_input_to_exfil",
        "overt_read_to_action",
    }


def test_mine_lsagm_attack_paths_finds_transform_carried_path() -> None:
    source = derive_capabilities(
        make_row(
            skill_id="skill/browser",
            name="Agent Browser",
            description="Browse websites and collect raw browser content.",
            requires_browser_access=1,
            requires_local_file_access=0,
            requires_docs=0,
            requires_email=0,
        )
    )
    intermediate = derive_capabilities(
        make_row(
            skill_id="skill/summary",
            author_handle="bob",
            name="Workspace Summarizer",
            description="Summarize browser content into workspace notes and memory for later use.",
            requires_browser_access=0,
            requires_local_file_access=1,
            requires_docs=1,
            requires_email=0,
        )
    )
    sink = derive_capabilities(
        make_row(
            skill_id="skill/slack",
            author_handle="carol",
            name="Slack API Gateway",
            description="Read workspace notes and send them to Slack via API.",
            requires_api_key=1,
            requires_slack=1,
            requires_local_file_access=1,
            requires_docs=1,
            requires_email=0,
        )
    )

    paths = mine_lsagm_attack_paths(
        [source, intermediate, sink],
        top_k=None,
        source_limit=10,
        intermediate_limit=10,
        sink_limit=10,
    )

    assert paths
    top = paths[0]
    assert top.source_skill_id == "skill/browser"
    assert top.intermediate_skill_id == "skill/summary"
    assert top.sink_skill_id == "skill/slack"
    assert top.carrier_type in {"workspace", "memory", "transform_buffer", "browser_state"}
    assert top.risk_score > 0


def test_source_sink_and_trust_taxonomy_are_derived() -> None:
    caps = derive_capabilities(
        make_row(
            skill_id="skill/notion_api",
            name="Notion API Gateway",
            description="Read Notion workspace docs and publish updates via API.",
            requires_api_key=1,
            requires_notion=1,
            requires_docs=1,
            requires_local_file_access=1,
        )
    )
    assert "sensitive_read" in source_types(caps)
    assert "contaminated_state" in source_types(caps)
    assert "exfil" in sink_types(caps) or "publish" in sink_types(caps)
    assert "EnterpriseInternal" in trust_domains(caps)
