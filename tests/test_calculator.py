from __future__ import annotations

import pytest

from akcalc.calculator import (
    calc_arts,
    calc_atk_final,
    calc_attack_interval,
    calc_final_damage,
    calc_healing,
    calc_physical,
    calc_true,
    evaluate_card,
    round_half_up,
)
from akcalc.schemas import Citation, FormulaCard


def test_physical_silverash_s3():
    r = calc_physical({"atk_final": 2139, "atk_multiplier": 1.0, "def_value": 500})
    assert r.raw_value == pytest.approx(1639)
    assert r.display_value == 1639


def test_physical_floor_uses_atk_final_not_multiplied():
    # 2139 - 2100 = 39, floor = 5% * 2139 = 106.95
    r = calc_physical({"atk_final": 2139, "atk_multiplier": 1.0, "def_value": 2100})
    assert r.raw_value == pytest.approx(106.95)
    assert r.display_value == 107  # half-up


def test_arts_eyja_s3():
    r = calc_arts({"atk_final": 1483.5, "atk_multiplier": 1.0, "res_value": 0.30})
    assert r.raw_value == pytest.approx(1038.45)
    assert r.display_value == 1038


def test_arts_floor_at_full_resistance():
    r = calc_arts({"atk_final": 1483.5, "atk_multiplier": 1.0, "res_value": 1.0})
    assert r.raw_value == pytest.approx(74.175)
    assert r.display_value == 74


def test_true_damage_ignores_def_res():
    r = calc_true({"atk_final": 1000, "atk_multiplier": 1.0, "def_value": 500, "res_value": 0.5})
    assert r.raw_value == 1000


def test_exusiai_s3_five_hits():
    r = calc_physical(
        {"atk_final": 540, "atk_multiplier": 1.1, "def_value": 200, "hits": 5}
    )
    assert r.raw_value == pytest.approx(1970)


def test_exusiai_floor_is_per_hit():
    # per hit: max(594 - 600, 27) = 27; total 135
    r = calc_physical(
        {"atk_final": 540, "atk_multiplier": 1.1, "def_value": 600, "hits": 5}
    )
    assert r.raw_value == pytest.approx(135)


def test_attack_interval_exusiai_s3():
    r = calc_attack_interval({"atk_time_base": 1.0, "atk_time_flat": -0.11})
    assert r.raw_value == pytest.approx(0.89)


def test_attack_interval_with_aspd():
    r = calc_attack_interval(
        {"atk_time_base": 1.0, "atk_time_flat": -0.11, "aspd_flat": 20}
    )
    assert r.raw_value == pytest.approx(0.89 * 100 / 120)


def test_attack_interval_eyja_s3():
    r = calc_attack_interval({"atk_time_base": 1.6, "atk_time_flat": -1.1})
    assert r.raw_value == pytest.approx(0.5)


def test_healing():
    r = calc_healing({"atk_final": 500, "heal_multiplier": 1.0, "healing_effect": 0.2})
    assert r.raw_value == pytest.approx(600)


def test_final_damage_fragile_and_reduction():
    r = calc_final_damage({"base_damage": 1000, "fragile": 0.3, "damage_reduction": 0.3})
    assert r.raw_value == pytest.approx(910)


def test_atk_final_percent_buff():
    r = calc_atk_final({"atk_base": 713, "atk_pct": 2.0})
    assert r.raw_value == pytest.approx(2139)


def test_round_half_up():
    assert round_half_up(99.5) == 100
    assert round_half_up(99.4) == 99
    assert round_half_up(100.5) == 101


def test_formula_card_and_python_agree():
    card = FormulaCard(
        id="physical_damage",
        name="物理伤害",
        expression=(
            "max(atk_final * atk_multiplier - max(def_value - def_ignore_flat, 0) "
            "* (1 - def_ignore_pct), 0.05 * atk_final) * hits"
        ),
        citation=Citation(source_id="test", title="test", url="https://example.com"),
    )
    result = evaluate_card(
        card,
        {"atk_final": 540, "atk_multiplier": 1.1, "def_value": 600, "hits": 5},
    )
    assert result.raw_value == pytest.approx(135)
    assert not result.warnings
