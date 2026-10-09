import sqlite3

from clawhub_analyzer.graph2_pairs import derive_graph2_profile, mine_graph2_pairs


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
        "name": "Workspace Helper",
        "downloads": 100,
        "stars": 10,
        "description": "Read private workspace documents and save a workspace note.",
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
        "requires_email": 0,
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


def test_derive_graph2_profile_detects_mediation_gates() -> None:
    profile = derive_graph2_profile(
        make_row(
            skill_id="skill/guarded",
            name="Config Guardian",
            description=(
                "Validate the config, preview changes, and require approval token before apply. "
                "Never modify the config unless policy allows it."
            ),
            requires_local_file_access=1,
            requires_shell_access=1,
        )
    )

    assert "Auth" in profile.mediation_gates
    assert "Validate" in profile.mediation_gates
    assert "Policy" in profile.mediation_gates
    assert "modify" in profile.sink_actions


def test_config_guardian_is_guarded_modify_sink() -> None:
    profile = derive_graph2_profile(
        make_row(
            skill_id="abdhilabs/config-guardian",
            name="Config Guardian",
            description=(
                "Safe OpenClaw config updates with automatic backup, validation, and rollback. "
                "For agent use - prevents invalid config updates."
            ),
            tags='["agent-tool", "backup", "config", "latest", "safety", "validation"]',
            requires_local_file_access=0,
            requires_shell_access=1,
            requires_docs=0,
        )
    )

    assert "workspace" in profile.channels
    assert "state_snapshot" in profile.input_objects
    assert "modify" in profile.sink_actions
    assert "Policy" in profile.mediation_gates


def test_openhue_is_control_sink_with_device_channel() -> None:
    profile = derive_graph2_profile(
        make_row(
            skill_id="steipete/openhue",
            name="Openhue",
            description="Control Philips Hue lights/scenes via the OpenHue CLI.",
            requires_local_file_access=0,
            requires_shell_access=0,
            requires_docs=0,
        )
    )

    assert "device_signal" in profile.channels
    assert "runtime_signal" in profile.input_objects
    assert "control" in profile.sink_actions


def test_graph2_pair_without_gate_becomes_unmediated_candidate() -> None:
    source = derive_graph2_profile(
        make_row(
            skill_id="skill/source",
            author_handle="alice",
            name="Workspace Reader",
            description="Read private workspace files and save private workspace notes for later use.",
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_graph2_profile(
        make_row(
            skill_id="skill/sink",
            author_handle="bob",
            name="Workspace Applier",
            description="Update and apply workspace config changes by saving the provided private file content.",
            requires_local_file_access=1,
            requires_shell_access=1,
            requires_docs=1,
        )
    )

    pairs = mine_graph2_pairs([source, sink], top_k=None)

    matching = [
        pair
        for pair in pairs
        if pair.source_skill_id == "skill/source" and pair.sink_skill_id == "skill/sink"
    ]

    assert matching
    assert matching[0].classification == "unmediated_candidate"
    assert "workspace" in matching[0].shared_channels
    assert "state_snapshot" in matching[0].shared_objects


def test_graph2_pair_with_gate_is_only_potentially_unsafe() -> None:
    source = derive_graph2_profile(
        make_row(
            skill_id="skill/source",
            author_handle="alice",
            name="Workspace Reader",
            description="Read private workspace files and save private workspace notes for later use.",
            requires_local_file_access=1,
            requires_docs=1,
        )
    )
    sink = derive_graph2_profile(
        make_row(
            skill_id="skill/sink",
            author_handle="bob",
            name="Guarded Workspace Applier",
            description=(
                "Validate and preview the provided private workspace note before saving. "
                "Require approval token before any apply operation."
            ),
            requires_local_file_access=1,
            requires_shell_access=1,
            requires_docs=1,
        )
    )

    pairs = mine_graph2_pairs([source, sink], top_k=None)

    matching = [
        pair
        for pair in pairs
        if pair.source_skill_id == "skill/source" and pair.sink_skill_id == "skill/sink"
    ]

    assert matching
    assert matching[0].classification == "potentially_unsafe"
    assert matching[0].gate_status == "present"
