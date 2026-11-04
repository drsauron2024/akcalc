from __future__ import annotations

from akcalc.calculator import append_damage_components, evaluate_card
from akcalc.planner import Planner
from akcalc.schemas import DamageComponent, Intent


def test_yato_s2_is_supported_mixed_damage():
    p = Planner()
    op, _ = p.resolve_operator("麒麟R夜刀二技能")
    assert op is not None
    skill = p.resolve_skill("麒麟R夜刀二技能", op)
    assert skill is not None
    assert skill.name == "乱舞"
    assert skill.calc_supported
    assert skill.hits == 16
    assert skill.damage_type == "physical"
    assert len(skill.extra_components) == 1
    comp = skill.extra_components[0]
    assert comp.damage_type == "arts"
    assert comp.atk_multiplier == 0.75
    assert comp.hits == 16


def test_yato_s2_plans_physical_even_when_question_mentions_res():
    p = Planner()
    plan = p.rule_plan("麒麟R夜刀二技能对防御500、法抗30的敌人造成多少伤害？")
    assert plan.intent == Intent.CALCULATE
    assert plan.formula_id == "physical_damage"
    assert plan.variable_bindings["hits"] == 16
    assert plan.variable_bindings["def_value"] == 500
    assert plan.variable_bindings["res_value"] == 0.3


def test_append_arts_component_to_physical_hit():
    p = Planner()
    plan = p.rule_plan("麒麟R夜刀二技能对防御500、法抗30的敌人造成多少伤害？")
    card = p.formula_by_id[plan.formula_id]
    result = evaluate_card(card, plan.variable_bindings)
    result = append_damage_components(
        result,
        [DamageComponent(damage_type="arts", atk_multiplier=0.75, hits=16, label="双雷剑麒麟")],
        plan.variable_bindings,
    )
    # physical: max(565*1.5-500, 0.05*565) * 16 = 5560
    # arts:     max(565*0.75*(1-0.3), 0.05*565) * 16 = 4746
    assert result.raw_value == 10306
    assert result.display_value == 10306
    assert "附加伤害" in result.formula_name
