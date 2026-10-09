from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(relative_path: str):
    path = ROOT / relative_path
    module_name = "test_script_" + relative_path.replace("/", "_").replace(".", "_")
    spec = spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_run_p25r_retryable_agent_error_covers_normalized_transient_markers() -> None:
    module = load_script_module("scripts/runtime_eval/run_p25r_pair_eval.py")

    assert module.classify_agent_infra_detail("source:service_unavailable") == "service_unavailable"
    assert module.classify_agent_infra_detail("sink:gateway_connect_failed") == "gateway_connect_failed"
    assert module.classify_agent_infra_detail("Command timed out after 20 seconds") == "timeout"
    assert module.is_retryable_agent_error("source:service_unavailable")
    assert module.is_retryable_agent_error("sink:gateway_connect_failed")
    assert module.is_retryable_agent_error("gateway connect failed while dialing upstream")
    assert module.is_retryable_agent_error("timed out waiting for agent response")
    assert not module.is_retryable_agent_error("permission denied")


def test_run_p25r_forwards_agent_timeout_to_openclaw_command(monkeypatch) -> None:
    module = load_script_module("scripts/runtime_eval/run_p25r_pair_eval.py")
    seen = {}

    class _Result:
        returncode = 0
        stdout = '{"result":{"payloads":[{"text":"OK"}]}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(module, "run", fake_run)

    result = module.run_agent(
        "Reply with exactly OK.",
        "probe-session",
        "p25r",
        17,
        stage="source",
    )

    assert result["text"] == "OK"
    assert "--timeout" in seen["cmd"]
    timeout_index = seen["cmd"].index("--timeout")
    assert seen["cmd"][timeout_index + 1] == "17"
    assert seen["kwargs"]["timeout"] == 17


def test_evidence_layers_format_share_preserves_small_nonzero_counts() -> None:
    module = load_script_module("scripts/build_bcr_cis_v3_evidence_layers.py")

    assert module.format_share(3, 10_000) == "<0.1%"
    assert module.format_share(0, 10_000) == "0.0%"
    assert module.format_share(66, 10_000) == "0.7%"


def test_analyze_p25r_results_filters_infra_trials_out_of_usable_counts(tmp_path: Path) -> None:
    module = load_script_module("scripts/runtime_eval/analyze_p25r_results.py")
    rows = [
        {
            "trial_id": "case1_contaminated_resource_r1",
            "case_id": "case1",
            "family": "booking_create",
            "arm": "contaminated_resource",
            "repeat_index": 1,
            "success": False,
            "aconc_violation": False,
            "evidence_mode": "infra_error",
            "detail": "INFRA: source:service_unavailable",
            "trial_dir": str(tmp_path / "case1"),
            "source_skill_id": "source/a",
            "sink_skill_id": "sink/a",
        },
        {
            "trial_id": "case2_same_pair_control_r1",
            "case_id": "case2",
            "family": "order_submit",
            "arm": "same_pair_control",
            "repeat_index": 1,
            "success": False,
            "aconc_violation": False,
            "evidence_mode": "negative",
            "detail": "no tool calls",
            "trial_dir": str(tmp_path / "case2"),
            "source_skill_id": "source/b",
            "sink_skill_id": "sink/b",
        },
    ]

    markdown = module.build_markdown(rows)

    assert "- completed trials: `2`" in markdown
    assert "- trials with infrastructure issues: `1`" in markdown
    assert "- usable trials after infra filtering: `1`" in markdown
    assert "- no-tool-call trials (usable only): `1`" in markdown


def test_c1_cas_role_and_hazard_heuristics() -> None:
    module = load_script_module("scripts/run_c1_cas_analysis.py")

    bridge_like = module.SkillSignals(
        skill_id="source/a",
        author_handle="alice",
        primary_source_type="contaminated_state",
        primary_domain="External",
        authority_surface=0.2,
        has_dangerous_sink=False,
        source_count=1,
        sink_count=0,
        has_browser_access=False,
        has_local_file_access=True,
        has_shell_access=False,
    )
    risky_sink = module.SkillSignals(
        skill_id="sink/a",
        author_handle="bob",
        primary_source_type="untrusted_input",
        primary_domain="AgentWorkspace",
        authority_surface=0.8,
        has_dangerous_sink=True,
        source_count=1,
        sink_count=1,
        has_browser_access=False,
        has_local_file_access=True,
        has_shell_access=True,
    )

    assert module.classify_source_singleton_role(bridge_like) == "bridge_only"
    assert module.classify_sink_singleton_hazard(risky_sink) == "likely_unsafe_alone"


def test_c1_seed_priority_prefers_same_turn_bridge_only_and_safer_singleton() -> None:
    module = load_script_module("scripts/build_c1_seed_pool.py")

    same_turn = {
        "composition_trigger_type": "same_turn",
        "source_singleton_role": "bridge_only",
        "sink_singleton_hazard": "likely_safe_alone",
        "composition_support_score": "0.8",
        "singleton_safety_plausibility": "0.7",
        "channel_confidence": "0.3",
        "sink_skill_id": "sink/one",
        "source_skill_id": "source/one",
    }
    sequential = {
        "composition_trigger_type": "sequential",
        "source_singleton_role": "mixed",
        "sink_singleton_hazard": "uncertain",
        "composition_support_score": "0.85",
        "singleton_safety_plausibility": "0.8",
        "channel_confidence": "0.4",
        "sink_skill_id": "sink/two",
        "source_skill_id": "source/two",
    }

    assert module.candidate_priority(same_turn) < module.candidate_priority(sequential)
