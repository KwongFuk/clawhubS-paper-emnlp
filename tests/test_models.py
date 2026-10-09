from clawhub_analyzer.models import PermissionOrSecretHint, Skill
from clawhub_analyzer.utils import parse_abbreviated_number


def test_parse_abbreviated_number() -> None:
    assert parse_abbreviated_number("19.6k") == 19_600
    assert parse_abbreviated_number("122k") == 122_000
    assert parse_abbreviated_number("2.3m") == 2_300_000
    assert parse_abbreviated_number("980") == 980


def test_models_defaults() -> None:
    skill = Skill(skill_id="owner/slug", page_url="https://clawhub.ai/owner/slug")
    assert skill.tags == []
    hint = PermissionOrSecretHint(skill_id="owner/slug")
    assert hint.requires_api_key is False
    assert hint.raw_evidence == []
