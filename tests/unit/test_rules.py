from pathlib import Path

import pytest
import yaml

from super6.rules import RulesValidationError, load_rules

REPO = Path(__file__).resolve().parents[2]


def _write(tmp_path: Path, **changes: object) -> Path:
    raw = yaml.safe_load((REPO / "configs" / "competition_rules.yaml").read_text())
    raw.update(changes)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_default_rules_load_and_are_flagged_unverified() -> None:
    rules = load_rules(REPO / "configs" / "competition_rules.yaml")
    assert (rules.points_exact_score, rules.points_correct_result_only) == (5, 2)
    assert rules.golden_goal_minute_indexing == "one_based"
    assert "RULES_UNVERIFIED" in rules.warnings()


def test_unknown_rule_blocks(tmp_path: Path) -> None:
    with pytest.raises(RulesValidationError):
        load_rules(_write(tmp_path, bonus_points_for_clean_sheet=1))


def test_unsupported_convention_blocks(tmp_path: Path) -> None:
    with pytest.raises(RulesValidationError):
        load_rules(_write(tmp_path, golden_goal_minute_indexing="zero_based"))


def test_inconsistent_points_block(tmp_path: Path) -> None:
    with pytest.raises(RulesValidationError):
        load_rules(_write(tmp_path, points_correct_result_only=6))


def test_verified_rules_need_a_date(tmp_path: Path) -> None:
    with pytest.raises(RulesValidationError):
        load_rules(_write(tmp_path, verified=True, verified_on=None))
    rules = load_rules(_write(tmp_path, verified=True, verified_on="2026-11-05"))
    assert rules.warnings() == []
