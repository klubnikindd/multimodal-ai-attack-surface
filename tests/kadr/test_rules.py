from __future__ import annotations

import pytest

from kadr.errors import RuleError
from kadr.rules.dsl import load_rules_dict
from kadr.rules.operators import evaluate_condition, evaluate_rule
from kadr.rules.presets import get_preset
from kadr.rules.templates import render_path_template, validate_template


def test_operators_all_any_not_and_ranges():
    context = {
        "kind": "photo",
        "camera.model": "Canon EOS R",
        "date": "2025-07-12T10:00:00",
        "tags": ["свадьба", "портрет"],
        "ai.confidence": 0.82,
        "size": 10_000,
        "gps.present": False,
    }
    assert evaluate_condition({"kind": {"in": ["photo", "raw"]}}, context)
    assert evaluate_condition({"camera.model": {"contains": "canon"}}, context)
    assert evaluate_condition({"date": {"between": ["2025-01-01", "2025-12-31"]}}, context)
    assert evaluate_condition({"date": {"gte": "2025-07-01"}}, context)
    assert evaluate_condition({"tags": {"any": ["свадьба"]}}, context)
    assert evaluate_condition({"tags": {"all": ["свадьба", "портрет"]}}, context)
    assert evaluate_condition({"not": {"kind": "video"}}, context)
    assert evaluate_condition({"ai.confidence": {"gte": 0.75}}, context)
    assert not evaluate_condition({"gps.present": {"eq": True}}, context)
    assert evaluate_condition({"kind": {"regex": "^pho"}}, context)


def test_path_template_sanitizes_and_preserves_date_subdirs():
    context = {
        "date": __import__("datetime").datetime(2024, 7, 15, 13, 22),
        "kind": "photo",
        "camera.model": "../../evil",
        "hash": "blake3:abcdef0123456789",
        "file.stem": "IMG_1",
    }
    rendered = render_path_template(
        "Archive/{date:%Y/%Y-%m-%d}/{camera.model|unknown}/{hash:8}", context
    )
    assert rendered.startswith("Archive/2024/2024-07-15/")
    assert rendered.endswith("/abcdef01")
    assert ".." not in rendered


def test_hash_token_non_numeric_format_is_rejected():
    with pytest.raises(RuleError):
        render_path_template("{hash:sha}", {"hash": "blake3:abcdef0123456789"})


def test_free_text_caption_is_forbidden_by_default():
    with pytest.raises(RuleError):
        validate_template("Private/{ai.caption_short}")
    assert render_path_template(
        "Private/{ai.caption_short}", {"ai.caption_short": "sunset"}, allow_free_text=True
    ).startswith("Private/")


def test_ruleset_validation_rejects_unsafe_mode_and_unknown_token():
    with pytest.raises(RuleError):
        load_rules_dict(
            {
                "version": 1,
                "safety": {"mode": "move", "never_delete": False},
                "rules": [{"id": "x", "then": {"path": "{date:%Y}"}}],
            }
        )
    with pytest.raises(RuleError):
        load_rules_dict(
            {
                "version": 1,
                "rules": [{"id": "x", "then": {"path": "{unknown.token}"}}],
                "fallback": {"path": "_Unsorted/{file.mtime:%Y}"},
            }
        )


def test_stop_rule_and_fallback_preset():
    preset = get_preset("date")
    ruleset = load_rules_dict(preset)
    assert ruleset.mode == "copy"
    context = {"kind": "photo", "date": __import__("datetime").datetime(2024, 7, 15), "ai.scene_type": None}
    decisions = [
        evaluate_rule(rule, context, vlm_allowed=False, allow_free_text_paths=False)
        for rule in ruleset.rules
    ]
    # default rule matches; screenshots/video/audio/raw не матчатся
    assert any(decision.matched for decision in decisions)


def test_vlm_rule_is_unavailable_on_lite_but_does_not_block():
    context = {"kind": "photo", "date": __import__("datetime").datetime(2025, 7, 15), "ai.event_type": None}
    decision = evaluate_rule(
        {
            "id": "wedding",
            "when": {"ai.event_type": {"eq": "wedding"}},
            "then": {"path": "Jobs/{date:%Y}"},
        },
        context,
        vlm_allowed=False,
    )
    assert decision.matched is False
    assert decision.unavailable_on_profile is True
    assert "ai.event_type" in (decision.reason or "")


def test_empty_rules_file_is_reported_as_empty(tmp_path):
    from kadr.rules.dsl import load_rules

    path = tmp_path / "rules.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(RuleError, match="пуст"):
        load_rules(path)
    with pytest.raises(RuleError, match="пуст"):
        load_rules_dict(None)  # type: ignore[arg-type]
