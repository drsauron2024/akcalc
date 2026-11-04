"""Generate curated and all-operator YAML from pinned game data.

- operators.yaml: the 6 operators used by the 20-question benchmark.
- operators_all.yaml: every playable CN operator (438 as of the pinned commit),
  all skills at their last mastery level, with a conservative calc_supported
  flag. Unsupported skills remain browsable but are refused for calculations.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GD_ROOT = ROOT / "data" / "raw" / "gamedata"

CURATED = {
    "银灰": "skchr_svrash_3",
    "艾雅法拉": "skchr_amgoat_3",
    "能天使": "skchr_angel_3",
    "史尔特尔": "skchr_surtr_3",
    "玛恩纳": "skchr_mlynar_3",
    "澄闪": "skchr_gdglow_3",
}

M3_INDEX = 9
HARD_UNSUPPORTED = [
    "召唤",
    "浮游",
    "陷阱",
    "持续伤害",
    "灼烧",
    "元素",
    "不攻击",
    "停止攻击",
    "再部署",
    "传送",
    "位移",
]
UNSUPPORTED_KEYS = [
    "ep_damage_ratio",
]
SOFT_NOTES = {
    "弹药": "按单发计算，不含弹药总数",
    "蓄力": "按蓄力后单次命中计算，不含蓄力时间",
    "闪避": "只计算伤害/治疗部分，闪避效果未计入",
    "屏障": "只计算伤害/治疗部分，屏障效果未计入",
    "护盾": "只计算伤害/治疗部分，护盾效果未计入",
    "嘲讽": "只计算伤害/治疗部分，嘲讽效果未计入",
    "停顿": "只计算伤害/治疗部分，控制效果未计入",
    "眩晕": "只计算伤害/治疗部分，控制效果未计入",
    "束缚": "只计算伤害/治疗部分，控制效果未计入",
}
SCALE_KEYS = [
    "attack@atk_scale",
    "atk_scale",
    "attack@aoe_atk_scale",
    "attack@main_atk_scale",
    "attack@ex_atk_scale",
    "attack@bounce_atk_scale",
    "attack@atk_scale_s3",
    "atk_scale_magic",
    "damage_scale",
]


def latest_gamedata() -> tuple[Path, dict]:
    candidates = sorted(GD_ROOT.glob("*/meta.json"))
    if not candidates:
        raise SystemExit("no gamedata found; run scripts/fetch_gamedata.py first")
    meta_path = candidates[-1]
    return meta_path.parent, json.loads(meta_path.read_text(encoding="utf-8"))


def blackboard_map(level: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in level.get("blackboard", []):
        key = item.get("key")
        value = item.get("value")
        if key is not None and value is not None:
            out[key] = float(value)
    return out


def skill_level(skill: dict) -> dict:
    levels = skill.get("levels") or []
    if not levels:
        return {}
    return levels[min(M3_INDEX, len(levels) - 1)]


def talent_damage_components(char: dict) -> list[dict]:
    """Find talents that add a fixed damage instance to every attack.

    Only deterministic, self-caused per-hit damage is modelled here; summons,
    traps and damage-over-time talents are still left to the unsupported path.
    """
    out: list[dict] = []
    for talent in char.get("talents") or []:
        candidates = talent.get("candidates") or []
        if not candidates:
            continue

        def rank(cand: dict) -> tuple[int, int]:
            phase = str((cand.get("unlockCondition") or {}).get("phase", "PHASE_0"))
            try:
                phase_num = int(phase.rsplit("_", 1)[-1])
            except ValueError:
                phase_num = 0
            return phase_num, -int(cand.get("requiredPotentialRank") or 0)

        cand = max(candidates, key=rank)
        desc = cand.get("description", "") or ""
        match = re.search(
            r"攻击(?:时)?额外造成攻击力\s*([\d.]+)\s*%\s*的?\s*(法术|物理|真实)伤害",
            desc,
        )
        if not match:
            continue
        dtype = {"法术": "arts", "物理": "physical", "真实": "true"}[match.group(2)]
        out.append(
            {
                "damage_type": dtype,
                "atk_multiplier": float(match.group(1)) / 100.0,
                "hits": 1,
                "label": cand.get("name") or "天赋附加伤害",
            }
        )
    return out


def talent_atk_bonuses(char: dict) -> list[str]:
    """Collect talent ATK% buffs that the simplified ATK model does not apply."""
    out: list[str] = []
    for talent in char.get("talents") or []:
        candidates = talent.get("candidates") or []
        if not candidates:
            continue

        def rank(cand: dict) -> tuple[int, int]:
            phase = str((cand.get("unlockCondition") or {}).get("phase", "PHASE_0"))
            try:
                phase_num = int(phase.rsplit("_", 1)[-1])
            except ValueError:
                phase_num = 0
            return phase_num, -int(cand.get("requiredPotentialRank") or 0)

        cand = max(candidates, key=rank)
        desc = cand.get("description", "") or ""
        match = re.search(r"攻击力\s*\+\s*([\d.]+)\s*%", desc)
        if match:
            out.append(f"{cand.get('name') or '天赋'} +{float(match.group(1)):g}%")
    return out


def parse_hits(desc: str, times: float | None) -> int:
    """Parse fixed hit counts such as '发动16次斩击' from the description."""
    desc = re.sub(r"<[^>]+>", "", desc or "")
    if times and float(times).is_integer() and times >= 1:
        return int(times)
    for pattern in (r"发动\s*(\d+)\s*次", r"(\d+)\s*次斩击", r"(\d+)\s*段"):
        match = re.search(pattern, desc)
        if match:
            return max(1, int(match.group(1)))
    # "二连击" is a fixed two-hit attack; "第三次攻击变为六连击" is a
    # conditional pattern and must not be collapsed into a single count.
    if "第三次" not in desc:
        match = re.search(r"([一二三四五六七八九十])连击", desc)
        if match:
            return {
                "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
            }[match.group(1)]
    return 1


def _best_talent_candidate(talent: dict) -> dict | None:
    candidates = talent.get("candidates") or []
    if not candidates:
        return None

    def rank(cand: dict) -> tuple[int, int]:
        phase = str((cand.get("unlockCondition") or {}).get("phase", "PHASE_0"))
        try:
            phase_num = int(phase.rsplit("_", 1)[-1])
        except ValueError:
            phase_num = 0
        return phase_num, -int(cand.get("requiredPotentialRank") or 0)

    return max(candidates, key=rank)


def trait_values(char: dict) -> tuple[float, float]:
    """Max trait ATK bonus and charge time (Liberator-style traits)."""
    trait = char.get("trait")
    if not isinstance(trait, dict):
        return 0.0, 0.0
    cand = _best_talent_candidate(trait)
    if not cand:
        return 0.0, 0.0
    bb = blackboard_map(cand)
    return float(bb.get("atk", 0.0) or 0.0), float(bb.get("max_stack_cnt", 0.0) or 0.0)


def talent_atk_scale(char: dict) -> tuple[float, str]:
    """Talent damage multiplier such as Mlynar's '攻击敌人时攻击力提升至110%'."""
    for talent in char.get("talents") or []:
        cand = _best_talent_candidate(talent)
        if not cand:
            continue
        bb = blackboard_map(cand)
        if "atk_scale_base" in bb:
            return float(bb["atk_scale_base"]), cand.get("name") or "天赋"
        match = re.search(r"攻击敌人时攻击力提升至\s*([\d.]+)\s*%", cand.get("description", "") or "")
        if match:
            return float(match.group(1)) / 100.0, cand.get("name") or "天赋"
    return 1.0, ""


def trait_text(char: dict) -> str:
    trait = char.get("trait")
    if isinstance(trait, str):
        return trait
    if isinstance(trait, dict):
        parts = []
        for cand in trait.get("candidates") or []:
            if cand.get("overrideDescripton"):
                parts.append(cand["overrideDescripton"])
        return " ".join(parts)
    return ""


def classify_damage_type(char: dict, desc: str, bb: dict[str, float]) -> str:
    trait = trait_text(char)
    text = trait + "\n" + desc
    desc_damage = ("伤害" in desc) or ("攻击" in desc and "攻击力" in desc)
    if "真实伤害" in text:
        return "true"
    # A medic's damage skill is not a healing skill: an explicit attack clause wins.
    if "治疗" in desc or (char.get("profession") == "MEDIC" and not desc_damage):
        return "healing"
    if "法术伤害" in text:
        return "arts"
    if "物理伤害" in text:
        return "physical"
    if char.get("profession") == "CASTER":
        return "arts"
    if char.get("profession") == "SUPPORT":
        return "arts"
    if char.get("subProfessionId") == "artsfghter":
        return "arts"
    return "physical"


def classify_skill(char: dict, skill: dict, level: dict, bb: dict[str, float]) -> dict:
    desc = level.get("description", "") or ""
    damage_type = classify_damage_type(char, desc, bb)
    atk_bonus = float(bb.get("atk", 0.0) or 0.0)

    atk_scale = None
    scale_key = ""
    for key in SCALE_KEYS:
        if key in bb and bb[key] is not None:
            atk_scale = bb[key]
            scale_key = key
            break
    heal_scale = bb.get("attack@heal_scale")
    if heal_scale is None:
        heal_scale = bb.get("heal_scale")
    times = bb.get("attack@times") or bb.get("times")
    aspd = float(bb.get("attack_speed", 0.0) or 0.0)
    atk_time_flat = float(bb.get("base_attack_time", 0.0) or 0.0)
    hits = parse_hits(desc, times)

    notes = [note for word, note in SOFT_NOTES.items() if word in desc]
    if scale_key in {"attack@aoe_atk_scale", "attack@bounce_atk_scale"}:
        notes.append("按单个目标计算，未乘目标数/弹射数")
    if "attack@trigger_time" in bb:
        notes.append("按单发计算，不含弹药总数")
    if scale_key in {"attack@main_atk_scale", "attack@ex_atk_scale", "attack@atk_scale_s3"}:
        notes.append("仅取主伤害倍率，未合并附加段")
    if (
        "attack@range_radius" in bb or (bb.get("attack@max_target") or 0) > 1
    ) and not any("按单个目标" in n for n in notes):
        notes.append("按单个目标计算，未乘目标数")
    if hits == 1 and "连击" in desc and not re.search(
        r"发动\s*\d+\s*次|(\d+)\s*次斩击", desc
    ):
        notes.append("按单次攻击计算，未乘连击段数")
    if "每突进" in desc:
        notes.append("按单段斩击/单个目标计算，未乘突进段数")
    trait_up = float(bb.get("trait_up", 1.0) or 1.0)
    per_kill_reduce = abs(float(bb.get("per_kill_reduce", 0.0) or 0.0))
    if trait_up != 1.0:
        notes.append(f"特性倍率 k 默认按满特性 {trait_up:g} 计算")
    if per_kill_reduce:
        notes.append(f"每击杀一名敌人 k -{per_kill_reduce:.0%}")
    if "卡西米尔" in desc and "真实" in desc:
        notes.append("未计入卡西米尔干员附加真实伤害")

    extra_components = talent_damage_components(char)
    talent_scale = float(bb.get("talent_scale", 1.0) or 1.0)
    if extra_components and damage_type in {"physical", "arts", "true"}:
        for component in extra_components:
            component["atk_multiplier"] = float(component["atk_multiplier"]) * talent_scale
            component["hits"] = hits
            if talent_scale != 1.0:
                component["label"] = f"{component['label']}（×{talent_scale:g}）"
        extra_names = "、".join(
            f"{c['label']} {c['atk_multiplier']:.0%}×{c['hits']}段" for c in extra_components
        )
        notes.append(f"含天赋附加伤害：{extra_names}")
    elif talent_scale != 1.0 and "第一天赋" in desc:
        notes.append("第一天赋倍率未计入伤害")
    if damage_type in {"physical", "arts", "true"}:
        atk_bonuses = talent_atk_bonuses(char)
        if atk_bonuses:
            notes.append("未计入天赋攻击力加成：" + "、".join(atk_bonuses))
    if hits > 1:
        notes.append(f"按 {hits} 段计算")
    calc_note = "；".join(dict.fromkeys(notes))

    reason = ""
    if any(word in desc for word in HARD_UNSUPPORTED):
        reason = "技能含召唤/浮游/陷阱/停止攻击/元素/持续伤害等未纳入的机制"
    if any(key in bb for key in UNSUPPORTED_KEYS):
        reason = reason or "技能含元素/天赋倍率等未纳入的机制"
    if damage_type == "healing":
        if heal_scale is None and atk_bonus == 0:
            reason = reason or "治疗量未给出可用的攻击力/倍率参数"
        if heal_scale is not None and "攻击力" not in desc:
            reason = reason or "按最大生命比例回复，不属于攻击力治疗公式"
    has_main = bool(atk_bonus or atk_scale is not None or aspd or heal_scale is not None)
    if not has_main and not reason:
        reason = "没有可识别的伤害/治疗/攻速数值"

    atk_multiplier = 1.0
    if atk_scale is not None:
        atk_multiplier = float(atk_scale)
    heal_multiplier = 1.0
    if heal_scale is not None:
        heal_multiplier = float(heal_scale)
    calc_supported = not reason
    return {
        "atk_bonus": atk_bonus,
        "atk_multiplier": atk_multiplier,
        "heal_multiplier": heal_multiplier,
        "atk_time_flat": atk_time_flat,
        "aspd_flat": aspd,
        "damage_type": damage_type,
        "hits": hits,
        "duration": bb.get("duration"),
        "extra_components": extra_components,
        "trait_up": trait_up,
        "per_kill_reduce": per_kill_reduce,
        "calc_supported": calc_supported,
        "unsupported_reason": reason,
        "calc_note": calc_note if calc_supported else "",
    }


def build_formula_note(
    atk: float,
    info: dict,
    trait_atk_bonus: float,
    trait_charge_time: float,
    talent_scale: float,
    talent_name: str,
) -> str:
    """Human-readable parameterized formula for time-varying trait skills."""
    if trait_atk_bonus <= 0 or info["damage_type"] not in {"physical", "arts", "true"}:
        return ""
    trait_up = float(info.get("trait_up", 1.0) or 1.0)
    per_kill = float(info.get("per_kill_reduce", 0.0) or 0.0)
    k_line = f"k = 满特性 {trait_up:g}"
    if trait_charge_time > 0:
        k_line += f"；未蓄满 k = {trait_up:g} × t / {trait_charge_time:g}"
    if per_kill > 0:
        k_line += f"；每击杀一名敌人 k −{per_kill:.0%}（最低 1.0）"
    atk_line = f"Atk_final(k) = {atk:g} × (1 + {trait_atk_bonus:g} × k)"
    mult = float(info.get("atk_multiplier", 1.0) or 1.0) * talent_scale
    if info["damage_type"] == "physical":
        damage_line = f"单目标伤害 = max(Atk_final × {mult:g} − DEF, 0.05 × Atk_final)"
    elif info["damage_type"] == "arts":
        damage_line = f"单目标伤害 = max(Atk_final × {mult:g} × (1 − RES), 0.05 × Atk_final)"
    else:
        damage_line = f"单目标伤害 = Atk_final × {mult:g}"
    talent_line = (
        f"其中 {mult:g} = 技能倍率 {info.get('atk_multiplier', 1.0):g} × 天赋「{talent_name}」{talent_scale:g}"
        if talent_scale != 1.0
        else ""
    )
    return "\n".join(x for x in [k_line, atk_line, damage_line, talent_line] if x)


def build_operator(char: dict, skill_table: dict, source: dict, keep_one_skill: bool = False) -> dict | None:
    phases = char.get("phases") or []
    if not phases:
        return None
    phase = phases[-1]
    attrs = phase["attributesKeyFrames"][-1]["data"]
    trait_atk_bonus, trait_charge_time = trait_values(char)
    talent_scale, talent_name = talent_atk_scale(char)
    skills = []
    for idx, ref in enumerate(char.get("skills") or [], start=1):
        sid = ref.get("skillId")
        skill = skill_table.get(sid)
        if not skill:
            continue
        level = skill_level(skill)
        if not level:
            continue
        bb = blackboard_map(level)
        info = classify_skill(char, skill, level, bb)
        info["formula_note"] = build_formula_note(
            float(attrs["atk"]),
            info,
            trait_atk_bonus,
            trait_charge_time,
            talent_scale,
            talent_name,
        )
        skills.append(
            {
                "id": sid,
                "operator_id": char["id"] if "id" in char else "",
                "name": level.get("name", ""),
                "skill_index": idx,
                "description": level.get("description", ""),
                **info,
            }
        )
        if keep_one_skill:
            break
    return {
        "id": char["id"],
        "name": char.get("name", ""),
        "class_name": char.get("profession", ""),
        "sub_profession": char.get("subProfessionId", ""),
        "trait": trait_text(char),
        "rarity": int(str(char.get("rarity", "TIER_6")).replace("TIER_", "")),
        "phase": 2,
        "level": int(phase["attributesKeyFrames"][-1]["level"]),
        "atk": float(attrs["atk"]),
        "def_value": float(attrs["def"]),
        "res_value": float(attrs["magicResistance"]) / 100.0,
        "hp": float(attrs["maxHp"]),
        "attack_interval": float(attrs["baseAttackTime"]),
        "trait_atk_bonus": trait_atk_bonus,
        "trait_charge_time": trait_charge_time,
        "talent_atk_scale": talent_scale,
        "skills": skills,
        "citation": source,
    }


def main() -> int:
    gd_dir, meta = latest_gamedata()
    chars = json.loads((gd_dir / "character_table.json").read_text(encoding="utf-8"))
    skills = json.loads((gd_dir / "skill_table.json").read_text(encoding="utf-8"))

    source = {
        "source_id": f"gamedata_{meta['commit'][:12]}",
        "title": "Kengxxiao/ArknightsGameData (CN official data extraction)",
        "url": f"https://github.com/Kengxxiao/ArknightsGameData/tree/{meta['commit']}/zh_CN/gamedata/excel",
        "section": "character_table.json / skill_table.json",
        "retrieved_at": meta["fetched_at"],
        "quote": "干员与技能数值来自固定 commit 的公开拆包数据",
    }

    playable = []
    for cid, char in chars.items():
        if char.get("profession") in ("TOKEN", "TRAP") or char.get("isNotObtainable"):
            continue
        char = {**char, "id": cid}
        op = build_operator(char, skills, source, keep_one_skill=False)
        if op and op["skills"]:
            playable.append(op)
    playable.sort(key=lambda x: (x["rarity"], x["name"]), reverse=True)

    all_out = {
        "version": f"gamedata-{meta['commit'][:12]}",
        "source": source,
        "operators": playable,
    }
    all_path = ROOT / "data" / "curation" / "operators_all.yaml"
    all_path.write_text(
        "# AUTO-GENERATED by scripts/build_operators.py; do not hand-edit.\n"
        + yaml.safe_dump(all_out, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    curated = []
    by_name = {op["name"]: op for op in playable}
    for name, sid in CURATED.items():
        op = by_name.get(name)
        if not op:
            print(f"WARN curated operator missing: {name}")
            continue
        chosen = next((s for s in op["skills"] if s["id"] == sid), None)
        if not chosen:
            print(f"WARN curated skill missing: {name} {sid}")
            continue
        curated.append({**op, "skills": [chosen]})
    curated_out = {
        "version": f"gamedata-{meta['commit'][:12]}",
        "source": source,
        "operators": curated,
    }
    curated_path = ROOT / "data" / "curation" / "operators.yaml"
    curated_path.write_text(
        "# AUTO-GENERATED curated benchmark subset; see operators_all.yaml.\n"
        + yaml.safe_dump(curated_out, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    supported = sum(1 for op in playable for s in op["skills"] if s["calc_supported"])
    total_skills = sum(len(op["skills"]) for op in playable)
    print(f"wrote {all_path} ({len(playable)} operators, {total_skills} skills, {supported} calc-supported)")
    print(f"wrote {curated_path} ({len(curated)} curated operators)")
    for op in playable[:5]:
        s = op["skills"][0]
        print(f"  {op['name']}: ATK={op['atk']:g} {s['name']} supported={s['calc_supported']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
