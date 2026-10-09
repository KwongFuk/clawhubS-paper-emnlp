from __future__ import annotations

from clawhub_analyzer.graph2_pairs import Graph2SkillProfile
from clawhub_analyzer.toxicity import SkillCapabilities
from clawhub_analyzer.sc_stateful_pairs import build_stateful_pair_row


def _caps(skill_id: str, name: str) -> SkillCapabilities:
    return SkillCapabilities(
        skill_id=skill_id,
        author_handle="author",
        name=name,
        downloads=100,
        stars=5,
        description="",
        tags="",
        metadata_systems="",
        untrusted_input=True,
        sensitive_read=False,
        critical_action=False,
        exfil_channel=False,
        credentialed=False,
        device_or_physical=False,
        memory_channel=False,
        workspace_channel=True,
        browser_state_channel=False,
        time_channel=False,
        clipboard_channel=False,
        keyboard_channel=False,
        mouse_channel=False,
        screen_channel=False,
        audio_channel=False,
        video_channel=False,
        desktop_state_channel=False,
        notification_channel=False,
        popup_channel=False,
        window_focus_channel=False,
        power_state_channel=False,
        transformer_or_stateful=False,
    )


def _profile(
    *,
    skill_id: str,
    name: str,
    source_labels: tuple[str, ...],
    sink_actions: tuple[str, ...],
    channels: tuple[str, ...],
    input_objects: tuple[str, ...],
    output_objects: tuple[str, ...],
    mediation_gates: tuple[str, ...] = (),
) -> Graph2SkillProfile:
    return Graph2SkillProfile(
        skill_id=skill_id,
        name=name,
        author_handle="author",
        downloads=100,
        stars=5,
        text="",
        hint_flags={},
        capabilities=_caps(skill_id, name),
        input_objects=input_objects,
        output_objects=output_objects,
        channels=channels,
        source_labels=source_labels,
        source_evidence=(),
        sink_actions=sink_actions,
        sink_evidence=(),
        mediation_gates=mediation_gates,
        domains=("External",),
    )


def test_build_stateful_pair_row_workspace_transact() -> None:
    source = _profile(
        skill_id="source/a",
        name="Source A",
        source_labels=("Tainted",),
        sink_actions=(),
        channels=("workspace",),
        input_objects=("raw_content",),
        output_objects=("private_content",),
    )
    sink = _profile(
        skill_id="sink/b",
        name="Sink B",
        source_labels=(),
        sink_actions=("transact",),
        channels=("workspace",),
        input_objects=("private_content",),
        output_objects=("state_snapshot",),
    )
    row = {
        "source_skill_id": source.skill_id,
        "sink_skill_id": sink.skill_id,
        "carrier_type": "workspace",
        "source_type": "contaminated_state",
        "sink_type": "transact",
        "shared_channels": "semantic_channel:workspace",
        "shared_objects": "object:private_content",
        "coinstall_surface": "workspace",
        "joint_invocation_surface": "same_turn_possible",
        "composition_trigger_type": "same_turn",
        "composition_support_score": "0.90",
        "singleton_safety_plausibility": "0.80",
        "channel_confidence": "0.50",
        "runnable_family": "workspace_reseller",
        "c1_dynamic_candidate": "True",
    }
    out = build_stateful_pair_row(row, {source.skill_id: source, sink.skill_id: sink})
    assert out["bridge_state"] == "workspace_private_context_ready"
    assert out["trigger_kind"] == "artifact_consumption"
    assert out["forbidden_effect"] == "unauthorized_subscription_change"
    assert "explicit_approval" in out["missing_guards"]


def test_build_stateful_pair_row_device_control() -> None:
    source = _profile(
        skill_id="source/device",
        name="Source Device",
        source_labels=("Sensitive",),
        sink_actions=(),
        channels=("device_signal",),
        input_objects=("state_snapshot",),
        output_objects=("runtime_signal",),
    )
    sink = _profile(
        skill_id="sink/device",
        name="Sink Device",
        source_labels=(),
        sink_actions=("control",),
        channels=("device_signal",),
        input_objects=("runtime_signal",),
        output_objects=("runtime_signal",),
    )
    row = {
        "source_skill_id": source.skill_id,
        "sink_skill_id": sink.skill_id,
        "carrier_type": "device_signal",
        "source_type": "contaminated_state",
        "sink_type": "control",
        "shared_channels": "system_channel:device_signal",
        "shared_objects": "object:runtime_signal",
        "coinstall_surface": "device_signal",
        "joint_invocation_surface": "parallel_possible",
        "composition_trigger_type": "same_turn",
        "composition_support_score": "0.85",
        "singleton_safety_plausibility": "0.75",
        "channel_confidence": "0.60",
        "runnable_family": "device_control",
        "c1_dynamic_candidate": "True",
    }
    out = build_stateful_pair_row(row, {source.skill_id: source, sink.skill_id: sink})
    assert out["trigger_kind"] == "threshold_rule"
    assert out["higher_level_conflict"] == "physical_or_operational_safety"
    assert out["forbidden_effect"] == "unauthorized_device_actuation"
    assert "physical_safety_interlock" in out["missing_guards"]
