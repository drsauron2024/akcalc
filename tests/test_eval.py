from __future__ import annotations

from akcalc.eval import extract_number


def test_boxed():
    assert extract_number(r"结果是 \[\boxed{107}\]") == 107


def test_bold_final():
    assert extract_number("最终结果：**2139**") == 2139


def test_total_damage_phrase():
    assert extract_number("五段总伤害为 1970 点。") == 1970


def test_interval_unit():
    assert extract_number(r"实际攻击间隔 = 0.89\ \text{秒}") == 0.89


def test_m3_marker_ignored():
    assert extract_number("能天使三技能 M3 的最终攻击力为 2139。") == 2139


def test_about_phrase():
    assert extract_number("所以单次伤害约为 1038（取整显示）。") == 1038


def test_no_number():
    assert extract_number("资料不足，无法计算。") is None
