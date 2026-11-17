"""DSL правил и шаблоны путей."""

from __future__ import annotations

from .dsl import RuleSet, load_rules, load_rules_dict, validate_rules
from .operators import evaluate_condition, evaluate_rule, is_vlm_field
from .presets import PRESETS, get_preset
from .templates import render_path_template, validate_template

__all__ = [
    "PRESETS",
    "RuleSet",
    "evaluate_condition",
    "evaluate_rule",
    "get_preset",
    "is_vlm_field",
    "load_rules",
    "load_rules_dict",
    "render_path_template",
    "validate_rules",
    "validate_template",
]
