from __future__ import annotations

import pytest

from akcalc.planner import Planner
from akcalc.schemas import Intent


def planner() -> Planner:
    return Planner()


def test_resolve_operator():
    p = planner()
    op, conf = p.resolve_operator("银灰三技能对防御500的伤害")
    assert op is not None and op.name == "银灰"
    assert conf >= 0.82


def test_physical_calculate_bindings():
    p = planner()
    plan = p.rule_plan("银灰三技能对防御500的敌人单次伤害是多少？")
    assert plan.intent == Intent.CALCULATE
    assert plan.formula_id == "physical_damage"
    assert plan.variable_bindings["atk_final"] == 2139
    assert plan.variable_bindings["def_value"] == 500


def test_floor_case_bindings():
    p = planner()
    plan = p.rule_plan("银灰三技能对防御2100的敌人单次伤害是多少？")
    assert plan.intent == Intent.CALCULATE
    assert plan.variable_bindings["def_value"] == 2100


def test_concept_is_formula_lookup():
    p = planner()
    plan = p.rule_plan("物理伤害怎么算？")
    assert plan.intent == Intent.FORMULA_LOOKUP
    assert plan.formula_id == "physical_damage"


def test_missing_def_clarifies():
    p = planner()
    plan = p.rule_plan("银灰三技能伤害是多少？")
    assert plan.intent == Intent.CLARIFY
    assert "def_value" in plan.missing_vars


def test_dps_missing_damage_clarifies():
    p = planner()
    plan = p.rule_plan("能天使三技能 DPS 是多少？")
    assert plan.intent == Intent.CLARIFY
    assert "damage_per_hit" in plan.missing_vars


def test_interval_calculate():
    p = planner()
    plan = p.rule_plan("能天使三技能 M3，再获得 ASPD +20，攻击间隔是多少秒？")
    assert plan.intent == Intent.CALCULATE
    assert plan.formula_id == "attack_interval"
    assert plan.variable_bindings["atk_time_base"] == 1.0
    assert plan.variable_bindings["atk_time_flat"] == -0.11
    assert plan.variable_bindings["aspd_flat"] == 20


def test_fragile_is_kept_for_chaining():
    p = planner()
    plan = p.rule_plan("银灰三技能对防御500的敌人攻击，敌人处于30%脆弱，最终伤害是多少？")
    assert plan.intent == Intent.CALCULATE
    assert plan.formula_id == "physical_damage"
    assert plan.variable_bindings["fragile"] == 0.3


def test_out_of_domain_refuses():
    p = planner()
    assert p.rule_plan("原神胡桃的伤害怎么算？").intent == Intent.REFUSE
    assert p.rule_plan("银灰的皮肤什么时候复刻？").intent == Intent.REFUSE


def test_mlynar_trait_is_parameterized():
    p = planner()
    plan = p.rule_plan("玛恩纳三技能对防御500的伤害是多少？")
    assert plan.intent == Intent.CALCULATE
    assert plan.variable_bindings["atk_final"] == 1775
    assert plan.variable_bindings["trait_multiplier"] == 2.0


def test_mlynar_kills_and_charge_time():
    p = planner()
    plan = p.rule_plan("玛恩纳三技能击杀3名敌人后对防御500的伤害是多少？")
    assert plan.variable_bindings["trait_multiplier"] == pytest.approx(1.7)
    assert plan.variable_bindings["atk_final"] == pytest.approx(1562)

    plan = p.rule_plan("玛恩纳三技能蓄力20秒对防御500的伤害是多少？")
    assert plan.variable_bindings["trait_multiplier"] == pytest.approx(1.0)
    assert plan.variable_bindings["atk_final"] == pytest.approx(1065)


def test_mlynar_formula_lookup_has_parameterized_note():
    p = planner()
    plan = p.rule_plan("玛恩纳三技能怎么算？")
    assert plan.intent == Intent.FORMULA_LOOKUP
    op, _ = p.resolve_operator("玛恩纳三技能怎么算？")
    skill = p.resolve_skill("玛恩纳三技能怎么算？", op)
    assert skill is not None and "k = 满特性" in skill.formula_note


def test_all_operators_loaded():
    p = planner()
    assert len(p.catalog.operators) >= 400
    assert sum(len(o.skills) for o in p.catalog.operators) >= 900


def test_entity_lookup_for_panel():
    p = planner()
    plan = p.rule_plan("能天使面板攻击力是多少？")
    assert plan.intent == Intent.ENTITY_LOOKUP
    assert plan.entity_links and plan.entity_links[0].canonical_id == "char_103_angel"


def test_skill_required_for_damage_without_skill():
    p = planner()
    plan = p.rule_plan("能天使伤害是多少？")
    assert plan.intent in (Intent.CLARIFY, Intent.REFUSE)
    assert any("技能" in m for m in plan.missing_vars) or plan.refusal_reason


def test_unsupported_full_operator_skill_refuses():
    p = planner()
    plan = p.rule_plan("澄闪三技能对防御500的伤害是多少？")
    assert plan.intent == Intent.REFUSE
    assert "暂不支持" in plan.refusal_reason


def test_longest_operator_name_wins():
    p = planner()
    op, _ = p.resolve_operator("凛御银灰三技能伤害")
    assert op is not None and op.name == "凛御银灰"
