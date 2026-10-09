from clawhub_analyzer.c1_wcr import (
    channel_alignment_score,
    static_carrier_to_cape_channels,
    when_reachability_tier,
)
from clawhub_analyzer.cape.skill_profile import SkillProfile, trigger_process_boost


def test_static_carrier_to_cape_channels_workspace():
    assert static_carrier_to_cape_channels("workspace") == ("C11", "C2")


def test_channel_alignment_score_prefers_exact_best_channel_match():
    score = channel_alignment_score("memory", ["C3", "C1"], "C3")
    assert score == 1.0


def test_when_reachability_tier_requires_explicit_when_for_highest_tier():
    assert when_reachability_tier(0.90, has_when_spec=True, alignment_score=0.80) == "W3_high"
    assert when_reachability_tier(0.90, has_when_spec=False, alignment_score=0.80) == "W2_medium"


def test_trigger_process_boost_for_device_context():
    source = SkillProfile(
        skill_id="src",
        name="src",
        source_caps=["web_fetch"],
        process_effects=["device_context", "status_signal"],
        output_type="FREE_TEXT",
    )
    sink = SkillProfile(
        skill_id="sink",
        name="sink",
        sink_caps=["network_send"],
        when_spec="when tasks involve finding Tasmota devices via network scanning, checking device status",
        invocation_clues=["explicit_when"],
    )
    assert trigger_process_boost(source, sink) >= 0.20
