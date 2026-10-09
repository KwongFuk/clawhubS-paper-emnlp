import sqlite3

from clawhub_analyzer.hypergraph import derive_hypergraph_profile
from clawhub_analyzer.pair_plausibility import (
    channel_confidence_evidence,
    ecological_filter_reason,
    pair_skill_text,
    plausibility_score,
    shared_clusters,
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


def test_channel_confidence_uses_install_command_and_not_scan_status() -> None:
    profile = derive_hypergraph_profile(
        make_row(
            skill_id="skill/plain",
            description="",
            tags="",
            metadata_systems="",
            install_command="npx workspace-sync --root ./workspace",
            scan_status="workspace suspicious",
        )
    )

    evidence = channel_confidence_evidence(profile, "workspace")

    assert evidence.support_count == 1
    assert evidence.matched_signals == ("install_command",)
    assert evidence.confidence == 0.2


def test_shared_clusters_detects_ecological_overlap() -> None:
    source = derive_hypergraph_profile(
        make_row(
            skill_id="skill/search",
            description="Search the web and summarize browser results into notes.",
            install_command="npx web-search",
        )
    )
    sink = derive_hypergraph_profile(
        make_row(
            skill_id="skill/browser-export",
            author_handle="bob",
            description="Export browser notes into a project workspace file.",
            install_command="npx workspace-export",
        )
    )

    overlap = shared_clusters(pair_skill_text(source), pair_skill_text(sink))

    assert overlap >= 1


def test_ecological_filter_reason_requires_clusters_and_source_usage() -> None:
    assert ecological_filter_reason(
        conf=0.3,
        src_installs=150,
        snk_installs=0,
        clusters=2,
    ) == "low_source_installs"
    assert ecological_filter_reason(
        conf=0.3,
        src_installs=250,
        snk_installs=0,
        clusters=0,
    ) == "no_shared_cluster"


def test_plausibility_score_normalizes_components() -> None:
    score = plausibility_score(conf=0.4, src_installs=50, snk_installs=25, clusters=2, max_installs=100)
    assert score == 1.8167
