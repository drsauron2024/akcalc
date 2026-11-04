"""Deterministic damage / healing / attack-speed calculations.

The LLM never does arithmetic: it only selects a formula and binds variables.
Every formula here is mirrored by a FormulaCard expression, and evaluate_card()
cross-checks the two implementations with SymPy.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

import sympy as sp

from .schemas import CalculationResult, CalculationStep, DamageComponent, FormulaCard


class MissingVariables(ValueError):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__("缺少变量: " + ", ".join(names))


def round_half_up(value: float, ndigits: int = 0) -> float:
    quantum = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _get(inputs: dict[str, float], name: str, default: float | None = None) -> float:
    if name in inputs:
        return float(inputs[name])
    if default is not None:
        return default
    raise MissingVariables([name])


def _result(
    formula_id: str,
    name: str,
    expression: str,
    inputs: dict[str, float],
    steps: list[CalculationStep],
    raw: float,
    unit: str,
    ndigits: int,
    warnings: list[str] | None = None,
) -> CalculationResult:
    return CalculationResult(
        formula_id=formula_id,
        formula_name=name,
        expression=expression,
        inputs={k: float(v) for k, v in inputs.items()},
        steps=steps,
        raw_value=raw,
        display_value=round_half_up(raw, ndigits),
        unit=unit,
        warnings=warnings or [],
    )


def calc_physical(inputs: dict[str, float]) -> CalculationResult:
    atk = _get(inputs, "atk_final")
    mult = _get(inputs, "atk_multiplier", 1.0)
    defense = _get(inputs, "def_value", 0.0)
    ignore_flat = _get(inputs, "def_ignore_flat", 0.0)
    ignore_pct = _get(inputs, "def_ignore_pct", 0.0)
    hits = _get(inputs, "hits", 1.0)
    steps = [CalculationStep(label="攻击力×技能倍率", expression=f"{atk:g} × {mult:g}", value=atk * mult)]
    eff_def = max(defense - ignore_flat, 0.0) * (1 - ignore_pct)
    steps.append(
        CalculationStep(
            label="有效防御力",
            expression=f"max({defense:g} - {ignore_flat:g}, 0) × (1 - {ignore_pct:g})",
            value=eff_def,
        )
    )
    per_hit = max(atk * mult - eff_def, 0.05 * atk)
    floor = 0.05 * atk
    steps.append(
        CalculationStep(
            label="单次物理伤害（含5%保底）",
            expression=f"max({atk * mult:g} - {eff_def:g}, 0.05 × {atk:g})",
            value=per_hit,
            note=f"5% 保底基于攻击力 {atk:g}，不是技能倍率后的 {atk * mult:g}",
        )
    )
    if atk * mult - eff_def < floor:
        steps[-1].note += "；本次触发保底"
    raw = per_hit * hits
    if hits != 1:
        steps.append(
            CalculationStep(
                label="多段伤害合计",
                expression=f"{per_hit:g} × {hits:g} 段",
                value=raw,
                note="保底按每一段分别结算",
            )
        )
    return _result(
        "physical_damage",
        "物理伤害",
        "max(atk_final * atk_multiplier - max(def_value - def_ignore_flat, 0) * (1 - def_ignore_pct), 0.05 * atk_final) * hits",
        inputs,
        steps,
        raw,
        "点",
        0,
    )


def calc_arts(inputs: dict[str, float]) -> CalculationResult:
    atk = _get(inputs, "atk_final")
    mult = _get(inputs, "atk_multiplier", 1.0)
    res = _get(inputs, "res_value", 0.0)
    ignore_flat = _get(inputs, "res_ignore_flat", 0.0)
    ignore_pct = _get(inputs, "res_ignore_pct", 0.0)
    hits = _get(inputs, "hits", 1.0)
    steps = [CalculationStep(label="攻击力×技能倍率", expression=f"{atk:g} × {mult:g}", value=atk * mult)]
    eff_res = max(res - ignore_flat, 0.0) * (1 - ignore_pct)
    steps.append(
        CalculationStep(
            label="有效法术抗性",
            expression=f"max({res:g} - {ignore_flat:g}, 0) × (1 - {ignore_pct:g})",
            value=eff_res,
        )
    )
    per_hit = max(atk * mult * (1 - eff_res), 0.05 * atk)
    floor = 0.05 * atk
    steps.append(
        CalculationStep(
            label="单次法术伤害（含5%保底）",
            expression=f"max({atk * mult:g} × (1 - {eff_res:g}), 0.05 × {atk:g})",
            value=per_hit,
            note=f"法术抗性按比例减伤；5% 保底基于攻击力 {atk:g}",
        )
    )
    if atk * mult * (1 - eff_res) < floor:
        steps[-1].note += "；本次触发保底"
    raw = per_hit * hits
    if hits != 1:
        steps.append(
            CalculationStep(
                label="多段伤害合计",
                expression=f"{per_hit:g} × {hits:g} 段",
                value=raw,
                note="保底按每一段分别结算",
            )
        )
    return _result(
        "arts_damage",
        "法术伤害",
        "max(atk_final * atk_multiplier * (1 - max(res_value - res_ignore_flat, 0) * (1 - res_ignore_pct)), 0.05 * atk_final) * hits",
        inputs,
        steps,
        raw,
        "点",
        0,
    )


def calc_true(inputs: dict[str, float]) -> CalculationResult:
    atk = _get(inputs, "atk_final")
    mult = _get(inputs, "atk_multiplier", 1.0)
    hits = _get(inputs, "hits", 1.0)
    per_hit = atk * mult
    raw = per_hit * hits
    steps = [
        CalculationStep(label="攻击力×技能倍率", expression=f"{atk:g} × {mult:g}", value=per_hit),
        CalculationStep(label="真实伤害（单次）", expression="不受 DEF/RES 影响", value=per_hit),
    ]
    if hits != 1:
        steps.append(
            CalculationStep(label="多段伤害合计", expression=f"{per_hit:g} × {hits:g} 段", value=raw)
        )
    return _result(
        "true_damage",
        "真实伤害",
        "atk_final * atk_multiplier * hits",
        inputs,
        steps,
        raw,
        "点",
        0,
    )


def append_damage_components(
    result: CalculationResult,
    components: list[DamageComponent],
    inputs: dict[str, float],
) -> CalculationResult:
    """Add deterministic per-hit damage components (e.g. talent arts damage).

    Each component is resolved with the same target DEF/RES as the main hit and
    independently applies the 5%-of-ATK floor. This keeps the physical and arts
    parts of a mixed-damage skill separate, instead of pretending the whole
    skill is a single damage type.
    """
    if not components:
        return result

    atk = _get(inputs, "atk_final")
    total = result.raw_value
    labels: list[str] = []
    expressions: list[str] = []
    for i, comp in enumerate(components):
        if comp.atk_multiplier <= 0 or comp.hits <= 0:
            continue
        sub_inputs: dict[str, float] = {
            "atk_final": atk,
            "atk_multiplier": comp.atk_multiplier,
            "hits": float(comp.hits),
        }
        for key in (
            "def_value",
            "def_ignore_flat",
            "def_ignore_pct",
            "res_value",
            "res_ignore_flat",
            "res_ignore_pct",
        ):
            if key in inputs:
                sub_inputs[key] = float(inputs[key])
        func = {"physical": calc_physical, "arts": calc_arts, "true": calc_true}[
            comp.damage_type
        ]
        sub = func(sub_inputs)
        label = comp.label or f"{comp.damage_type} 附加伤害"
        for step in sub.steps:
            step.label = f"{label}·{step.label}"
        result.steps.extend(sub.steps)
        result.steps.append(
            CalculationStep(
                label=f"{label}合计",
                expression=f"{sub.raw_value:g}",
                value=sub.raw_value,
                note=f"{comp.atk_multiplier:.0%} × 攻击力 × {comp.hits} 段",
            )
        )
        total += sub.raw_value
        labels.append(f"{label} {comp.atk_multiplier:.0%}×{comp.hits}段")
        expressions.append(f"{comp.atk_multiplier:g}*atk_final*{comp.hits}")
        result.inputs[f"extra_{i}_atk_multiplier"] = comp.atk_multiplier
        result.inputs[f"extra_{i}_hits"] = float(comp.hits)

    if not labels:
        return result
    result.raw_value = total
    result.display_value = round_half_up(total)
    result.formula_name = f"{result.formula_name} + 附加伤害"
    result.expression = f"{result.expression} + " + " + ".join(expressions)
    result.warnings.append("附加伤害按每段独立结算，物理/法术分别套用目标 DEF/RES")
    return result


def calc_healing(inputs: dict[str, float]) -> CalculationResult:
    atk = _get(inputs, "atk_final")
    mult = _get(inputs, "heal_multiplier", 1.0)
    bonus = _get(inputs, "healing_effect", 0.0)
    raw = atk * mult * (1 + bonus)
    steps = [
        CalculationStep(label="治疗量", expression=f"{atk:g} × {mult:g} × (1 + {bonus:g})", value=raw)
    ]
    return _result(
        "healing",
        "治疗量",
        "atk_final * heal_multiplier * (1 + healing_effect)",
        inputs,
        steps,
        raw,
        "点",
        0,
    )


def calc_attack_interval(inputs: dict[str, float]) -> CalculationResult:
    base = _get(inputs, "atk_time_base")
    direct = _get(inputs, "atk_time_flat", 0.0)
    flat = _get(inputs, "aspd_flat", 0.0)
    pct = _get(inputs, "aspd_pct", 0.0)
    atk_time = max(base + direct, 0.0)
    raw = atk_time * 100 / ((100 + flat) * (1 + pct))
    steps = [
        CalculationStep(
            label="直接攻击间隔修正",
            expression=f"max({base:g} + ({direct:g}), 0)",
            value=atk_time,
            note="技能/天赋的直接攻击间隔修正先结算，再加 ASPD",
        ),
        CalculationStep(
            label="攻击间隔",
            expression=f"{atk_time:g} × 100 / ((100 + {flat:g}) × (1 + {pct:g}))",
            value=raw,
            note="ASPD 默认 100；+15 ASPD 即攻击间隔缩短约 13.0%",
        ),
    ]
    return _result(
        "attack_interval",
        "攻击间隔",
        "max(atk_time_base + atk_time_flat, 0) * 100 / ((100 + aspd_flat) * (1 + aspd_pct))",
        inputs,
        steps,
        raw,
        "秒",
        3,
    )


def calc_dps(inputs: dict[str, float]) -> CalculationResult:
    dmg = _get(inputs, "damage_per_hit")
    interval = _get(inputs, "interval")
    if interval <= 0:
        raise ValueError("interval must be positive")
    raw = dmg / interval
    steps = [
        CalculationStep(label="DPS", expression=f"{dmg:g} / {interval:g}", value=raw)
    ]
    return _result(
        "dps", "每秒伤害", "damage_per_hit / interval", inputs, steps, raw, "点/秒", 2
    )


def calc_final_damage(inputs: dict[str, float]) -> CalculationResult:
    base = _get(inputs, "base_damage")
    fragile = _get(inputs, "fragile", 0.0)
    reduction = _get(inputs, "damage_reduction", 0.0)
    if not 0 <= reduction < 1:
        raise ValueError("damage_reduction must be in [0, 1)")
    raw = base * (1 + fragile) * (1 - reduction)
    steps = [
        CalculationStep(
            label="最终伤害",
            expression=f"{base:g} × (1 + {fragile:g}) × (1 - {reduction:g})",
            value=raw,
            note="脆弱同类只取最高值；减伤按乘法叠加",
        )
    ]
    return _result(
        "final_damage",
        "最终伤害（含脆弱/减伤）",
        "base_damage * (1 + fragile) * (1 - damage_reduction)",
        inputs,
        steps,
        raw,
        "点",
        0,
    )


def calc_atk_final(inputs: dict[str, float]) -> CalculationResult:
    base = _get(inputs, "atk_base")
    flat = _get(inputs, "atk_flat", 0.0)
    pct = _get(inputs, "atk_pct", 0.0)
    extra = _get(inputs, "atk_ex", 0.0)
    raw = (base + flat) * (1 + pct) + extra
    steps = [
        CalculationStep(
            label="最终攻击力",
            expression=f"({base:g} + {flat:g}) × (1 + {pct:g}) + {extra:g}",
            value=raw,
            note="ATK% 加成按加法合并；该式用于同类型增益，复杂跨天赋叠加需另行校订",
        )
    ]
    return _result(
        "atk_final",
        "最终攻击力",
        "(atk_base + atk_flat) * (1 + atk_pct) + atk_ex",
        inputs,
        steps,
        raw,
        "点",
        1,
    )


FORMULA_FUNCS: dict[str, Callable[[dict[str, float]], CalculationResult]] = {
    "physical_damage": calc_physical,
    "arts_damage": calc_arts,
    "true_damage": calc_true,
    "healing": calc_healing,
    "attack_interval": calc_attack_interval,
    "dps": calc_dps,
    "final_damage": calc_final_damage,
    "atk_final": calc_atk_final,
}


def sympy_check(card: FormulaCard, inputs: dict[str, float]) -> float | None:
    """Evaluate the FormulaCard expression independently of the Python function."""
    try:
        local = {k: sp.Float(v) for k, v in inputs.items()}
        expr = sp.sympify(card.expression, locals=local)
        value = float(sp.N(expr))
        if math.isnan(value):
            return None
        return value
    except Exception:  # noqa: BLE001
        return None


def evaluate_card(card: FormulaCard, inputs: dict[str, float]) -> CalculationResult:
    func = FORMULA_FUNCS.get(card.id)
    if func is None:
        raise KeyError(f"no calculator registered for formula {card.id!r}")
    result = func(inputs)
    result.formula_name = card.name
    result.expression = card.expression
    result.citations = [card.citation]
    check = sympy_check(card, inputs)
    if check is not None and abs(check - result.raw_value) > max(1e-6, abs(check) * 1e-9):
        result.warnings.append(
            f"公式卡表达式与计算器结果不一致：sympy={check:.6f}, python={result.raw_value:.6f}"
        )
    return result
