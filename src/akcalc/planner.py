"""Query planning: entity linking, formula selection and variable binding.

The rule planner handles the curated question space deterministically. An LLM
fallback only proposes a plan (intent/formula/variables); it never does math.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from .config import PROJECT_ROOT
from .llm import client
from .schemas import EntityLink, FormulaCard, Intent, OperatorRecord, Plan, RetrievedDoc

CURATION = PROJECT_ROOT / "data" / "curation"

OUT_OF_DOMAIN = [
    "原神",
    "崩坏",
    "星穹铁道",
    "王者荣耀",
    "英雄联盟",
    "卡池",
    "抽卡",
    "立绘",
    "皮肤",
    "剧情",
    "更新时间",
    "维护",
    "公招",
    "招募",
    "基建",
    "搓玉",
    "活动时间",
    "干员档案",
]


FORMULA_KEYWORDS: list[tuple[str, str]] = [
    ("攻击间隔", "attack_interval"),
    ("攻速", "attack_interval"),
    ("aspd", "attack_interval"),
    ("dps", "dps"),
    ("秒伤", "dps"),
    ("每秒伤害", "dps"),
    ("治疗", "healing"),
    ("奶量", "healing"),
    ("回血", "healing"),
    ("脆弱", "final_damage"),
    ("减伤", "final_damage"),
    ("最终伤害", "final_damage"),
    ("真实伤害", "true_damage"),
    ("真伤", "true_damage"),
    ("法术", "arts_damage"),
    ("法伤", "arts_damage"),
    ("法抗", "arts_damage"),
    ("物理", "physical_damage"),
    ("物伤", "physical_damage"),
    ("防御力", "physical_damage"),
    ("攻击力加成", "atk_final"),
    ("最终攻击力", "atk_final"),
]


@dataclass
class Catalog:
    formulas: list[FormulaCard]
    operators: list[OperatorRecord]


def load_catalog() -> Catalog:
    f_raw = yaml.safe_load((CURATION / "formulas.yaml").read_text(encoding="utf-8"))
    op_path = CURATION / "operators_all.yaml"
    if not op_path.exists():
        op_path = CURATION / "operators.yaml"
    o_raw = yaml.safe_load(op_path.read_text(encoding="utf-8"))
    return Catalog(
        formulas=[FormulaCard.model_validate(x) for x in f_raw["formulas"]],
        operators=[OperatorRecord.model_validate(x) for x in o_raw["operators"]],
    )


class Planner:
    def __init__(self, catalog: Catalog | None = None):
        self.catalog = catalog or load_catalog()
        self.formula_by_id = {f.id: f for f in self.catalog.formulas}
        self.operator_by_name = {o.name: o for o in self.catalog.operators}

    # ---------- entity resolution ----------

    def resolve_operator(self, query: str) -> tuple[OperatorRecord | None, float]:
        # Longest name first: "凛御银灰" must win over "银灰" when both match.
        ordered = sorted(self.catalog.operators, key=lambda o: len(o.name), reverse=True)
        matches = [op for op in ordered if op.name and op.name in query]
        if matches:
            return matches[0], 1.0
        best: OperatorRecord | None = None
        best_score = 0.0
        for op in ordered:
            score = fuzz.partial_ratio(op.name, query) / 100.0
            if score > best_score:
                best, best_score = op, score
        if best_score >= 0.82:
            return best, best_score
        return None, 0.0

    def resolve_skill(self, query: str, op: OperatorRecord | None):
        if not op or not op.skills:
            return None
        for sk in op.skills:
            if sk.name and sk.name in query:
                return sk
        m = re.search(r"([一二三123])\s*技能", query)
        if m:
            ch = m.group(1)
            idx = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}[ch]
            for sk in op.skills:
                if sk.skill_index == idx:
                    return sk
        return None

    # ---------- variable extraction ----------

    @staticmethod
    def _pct(value: str) -> float:
        v = float(value)
        return v / 100.0 if v > 1 else v

    def extract_variables(
        self, query: str, formula_id: str, op: OperatorRecord | None, skill
    ) -> dict[str, float]:
        q = query.replace("％", "%")
        v: dict[str, float] = {}

        m = re.search(r"(?:防御力?|DEF)\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)", q, re.I)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:点)?\s*防御", q)
        if m:
            v["def_value"] = float(m.group(1))

        m = re.search(r"(?:法术抗性|法抗|RES)\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*%?", q, re.I)
        if m:
            v["res_value"] = self._pct(m.group(1))

        m = re.search(
            r"(?<!基础)(?<!面板)(?:攻击力)\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*(?!%)", q
        )
        if m:
            v["atk_final"] = float(m.group(1))

        m = re.search(r"攻击力\s*\+?\s*(\d+(?:\.\d+)?)\s*%", q)
        if m:
            v["atk_pct"] = self._pct(m.group(1))

        m = re.search(r"(?:技能)?倍率\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)", q)
        if m:
            v["atk_multiplier"] = float(m.group(1))

        m = re.search(r"(\d+)\s*(?:连射|段|次)", q)
        if m:
            v["hits"] = float(m.group(1))

        m = re.search(r"(?:ASPD|攻速)\s*\+?\s*(\d+(?:\.\d+)?)", q, re.I)
        if m:
            v["aspd_flat"] = float(m.group(1))

        m = re.search(r"攻击间隔\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*秒", q)
        if m:
            v["atk_time_base"] = float(m.group(1))

        m = re.search(r"脆弱\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*%?", q)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*的?\s*脆弱", q)
        if m:
            v["fragile"] = self._pct(m.group(1))

        m = re.search(r"减伤\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*%?", q)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*的?\s*减伤", q)
        if m:
            v["damage_reduction"] = self._pct(m.group(1))

        m = re.search(r"治疗(?:增益|效果|加成)\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)\s*%?", q)
        if m:
            v["healing_effect"] = self._pct(m.group(1))

        m = re.search(r"基础伤害\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)", q)
        if m:
            v["base_damage"] = float(m.group(1))
        m = re.search(r"单次伤害\s*(?:为|是|[:=])?\s*(\d+(?:\.\d+)?)", q)
        if m:
            v["damage_per_hit"] = float(m.group(1))

        if op and skill:
            if formula_id in {"physical_damage", "arts_damage", "true_damage"}:
                if "atk_final" not in v:
                    bonus = v.get("atk_pct", skill.atk_bonus)
                    v["atk_final"] = op.atk * (1.0 + bonus)
                v.setdefault("atk_multiplier", skill.atk_multiplier)
                v.setdefault("hits", float(skill.hits))
            elif formula_id == "attack_interval":
                v.setdefault("atk_time_base", op.attack_interval)
                v.setdefault("atk_time_flat", skill.atk_time_flat)
                v.setdefault("aspd_flat", skill.aspd_flat)
            elif formula_id == "atk_final":
                v.setdefault("atk_base", op.atk)
                v.setdefault("atk_pct", skill.atk_bonus)
            elif formula_id == "healing":
                v.setdefault("atk_final", op.atk)
                v.setdefault("heal_multiplier", skill.heal_multiplier)
            elif formula_id == "dps":
                v.setdefault("interval", op.attack_interval)
                if skill.atk_time_flat:
                    v["interval"] = max(op.attack_interval + skill.atk_time_flat, 0.0)
                if "damage_per_hit" not in v:
                    # Let the agent chain a damage calculation if enemy defense is known.
                    pass
        return v

    # ---------- rule plan ----------

    def _detect_formula(self, query: str, op: OperatorRecord | None, skill) -> str | None:
        q = query.lower()
        # "银灰三技能打防御500的敌人，脆弱30%" -> compute physical damage, then
        # let the agent chain the fragile/damage-reduction card on top.
        if (
            op
            and skill
            and any(w in q for w in ["脆弱", "减伤", "最终伤害"])
            and any(w in q for w in ["物理", "法术", "伤害", "防御", "法抗"])
            and "基础伤害" not in q
        ):
            return {
                "physical": "physical_damage",
                "arts": "arts_damage",
                "true": "true_damage",
                "healing": "healing",
            }.get(skill.damage_type)
        if op and skill:
            # Once the operator and skill are known, the skill's own damage type
            # wins over incidental words such as "法抗" or "防御力" in the
            # question. Those words describe the target, not the formula.
            if any(keyword in q for keyword in ["攻击间隔", "攻速", "aspd"]):
                return "attack_interval"
            if any(keyword in q for keyword in ["dps", "秒伤", "每秒伤害"]):
                return "dps"
            # "银灰三技能攻击力是多少" asks for the buffed ATK, not damage.
            if "攻击力" in q and not any(
                k in q for k in ["伤害", "防御", "法抗", "dps", "每秒", "间隔"]
            ):
                return "atk_final"
            return {
                "physical": "physical_damage",
                "arts": "arts_damage",
                "true": "true_damage",
                "healing": "healing",
            }.get(skill.damage_type)

        for keyword, formula_id in FORMULA_KEYWORDS:
            if keyword in q:
                return formula_id
        if "保底" in q or "5%" in q:
            return "physical_damage"
        return None

    def rule_plan(self, query: str) -> Plan:
        q = query.lower()
        op, op_conf = self.resolve_operator(query)
        skill = self.resolve_skill(query, op)
        formula_id = self._detect_formula(query, op, skill)

        requested_skill = re.search(r"([一二三123])\s*技能", query)
        if op and skill is None and requested_skill:
            available = "、".join(f"{s.skill_index}.{s.name}" for s in op.skills)
            return Plan(
                intent=Intent.CLARIFY,
                entity_links=[
                    EntityLink(mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf)
                ],
                missing_vars=[f"技能（{op.name}可用：{available}）"],
                confidence=0.8,
                reasoning="requested skill index does not exist",
            )

        if any(word in query for word in ["原神", "崩坏", "星穹铁道", "王者荣耀", "英雄联盟"]):
            return Plan(
                intent=Intent.REFUSE,
                refusal_reason="该问题属于其他游戏，当前知识库只覆盖《明日方舟》的数值公式。",
                confidence=0.95,
                reasoning="other-game keyword",
            )
        if any(word in query for word in OUT_OF_DOMAIN) and not formula_id:
            return Plan(
                intent=Intent.REFUSE,
                refusal_reason="当前知识库覆盖明日方舟全干员实体与伤害/治疗/攻速公式，不包含该问题。",
                confidence=0.9,
                reasoning="out-of-domain keyword",
            )

        # Pure stat/skill lookup: all 421 playable operators are browsable.
        STAT_WORDS = [
            "攻击力", "防御力", "法抗", "生命", "面板", "属性", "费用",
            "阻挡", "职业", "稀有度", "技能", "攻击间隔", "攻速",
        ]
        if op and not skill and any(word in query for word in STAT_WORDS):
            return Plan(
                intent=Intent.ENTITY_LOOKUP,
                entity_links=[
                    EntityLink(
                        mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf
                    )
                ],
                confidence=0.85,
                reasoning="operator entity lookup",
            )

        if op and any(word in query for word in ["立绘", "皮肤", "剧情", "卡池", "档案"]):
            return Plan(
                intent=Intent.REFUSE,
                refusal_reason="当前知识库只覆盖数值计算，不包含干员立绘、剧情或卡池信息。",
                confidence=0.9,
            )

        calc_formulas = {
            "physical_damage",
            "arts_damage",
            "true_damage",
            "healing",
            "dps",
            "attack_interval",
        }
        if op and skill and not skill.calc_supported and formula_id in calc_formulas:
            return Plan(
                intent=Intent.REFUSE,
                entity_links=[
                    EntityLink(mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf),
                    EntityLink(mention=skill.name, canonical_id=skill.id, kind="skill", confidence=0.9),
                ],
                refusal_reason=(
                    f"{op.name} 的「{skill.name}」暂不支持自动计算："
                    f"{skill.unsupported_reason or '技能机制未纳入当前模型'}。"
                    "可以查询面板与技能描述，或换一个技能。"
                ),
                confidence=0.9,
            )

        if op and skill is None and formula_id in calc_formulas and not any(
            word in query for word in ["公式", "怎么算", "原理", "是什么"]
        ):
            return Plan(
                intent=Intent.CLARIFY,
                entity_links=[
                    EntityLink(mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf)
                ],
                missing_vars=["技能"],
                confidence=0.75,
                reasoning="识别到干员，但未指定技能",
            )

        if formula_id is None:
            if op and op_conf >= 0.82:
                return Plan(
                    intent=Intent.CLARIFY,
                    entity_links=[EntityLink(mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf)],
                    missing_vars=["技能或查询类型"],
                    confidence=0.6,
                    reasoning="识别到干员，但无法确定要查询公式还是数值",
                )
            if "伤害" in query:
                return Plan(
                    intent=Intent.CLARIFY,
                    missing_vars=["伤害类型"],
                    confidence=0.5,
                    reasoning="未识别具体公式",
                )
            return Plan(
                intent=Intent.REFUSE,
                refusal_reason="没有在知识库中找到与该问题匹配的公式或干员。",
                confidence=0.7,
            )

        card = self.formula_by_id.get(formula_id)
        if card is None:
            return Plan(intent=Intent.REFUSE, refusal_reason="公式不在知识库中。", confidence=0.8)

        variables = self.extract_variables(query, formula_id, op, skill)
        entity_links: list[EntityLink] = []
        if op:
            entity_links.append(
                EntityLink(mention=op.name, canonical_id=op.id, kind="operator", confidence=op_conf)
            )
        if skill:
            entity_links.append(
                EntityLink(mention=skill.name, canonical_id=skill.id, kind="skill", confidence=0.9)
            )

        required = [v.symbol for v in card.variables if v.required]
        missing = [name for name in required if name not in variables]

        numeric_intent = any(
            key
            for key in variables
            if key
            in {
                "def_value",
                "res_value",
                "atk_final",
                "atk_base",
                "aspd_flat",
                "fragile",
                "damage_reduction",
                "base_damage",
                "damage_per_hit",
                "heal_multiplier",
                "healing_effect",
                "hits",
            }
        )
        if card.id == "attack_interval" and (op or "攻击间隔" in query):
            numeric_intent = True
        if card.id == "atk_final" and (op or "攻击力" in query):
            numeric_intent = True
        if card.id == "dps" and op:
            numeric_intent = True

        # A concept question ("物理伤害怎么算？") has no numbers and is a lookup,
        # even though the formula's required variables are not bound.
        if missing and numeric_intent:
            return Plan(
                intent=Intent.CLARIFY,
                formula_id=formula_id,
                entity_links=entity_links,
                variable_bindings=variables,
                missing_vars=missing,
                confidence=0.75,
                reasoning="公式已匹配，但缺少必需变量",
            )

        intent = Intent.CALCULATE if numeric_intent else Intent.FORMULA_LOOKUP
        return Plan(
            intent=intent,
            formula_id=formula_id,
            entity_links=entity_links,
            variable_bindings=variables,
            confidence=0.85 if numeric_intent else 0.8,
            reasoning="rule planner",
        )

    # ---------- LLM fallback ----------

    def llm_plan(self, query: str, retrieved: list[RetrievedDoc]) -> Plan | None:
        if not client.enabled:
            return None
        formula_lines = []
        for card in self.catalog.formulas:
            vars_ = ", ".join(f"{v.symbol}({v.name})" for v in card.variables)
            formula_lines.append(f"- {card.id}: {card.name}; 变量: {vars_}")
        op_names = {d.title for d in retrieved if d.kind == "operator"}
        op_names |= {o.name for o in self.catalog.operators if o.name and o.name in query}
        op_lines = [
            f"- {o.name}: ATK={o.atk:g}, 攻击间隔={o.attack_interval:g}s, "
            f"技能={', '.join(s.name for s in o.skills[:3]) or '无'}"
            for o in self.catalog.operators
            if o.name in op_names
        ][:12] or ["（未检索到具体干员）"]
        context = "\n".join(f"[{d.doc_id}] {d.text[:500]}" for d in retrieved[:6])
        prompt = f"""你是《明日方舟》数值问题的规划器。只输出 JSON，不要做任何算术。

可用公式：
{chr(10).join(formula_lines)}

可用干员：
{chr(10).join(op_lines)}

检索到的资料：
{context}

用户问题：{query}

输出 JSON 字段：
- intent: "calculate" | "formula_lookup" | "entity_lookup" | "clarify" | "refuse"
- formula_id: 上面的公式 id，无法判断则为 null
- entity_links: [{{"mention": "...", "canonical_id": "...", "kind": "operator|skill|formula|unknown"}}]
- variable_bindings: 对象；百分比一律用小数（30% -> 0.3），防御/攻击/伤害用原数值
- missing_vars: 公式必需但问题里没有提供的变量名列表
- refusal_reason: 拒答原因，可为空
- confidence: 0 到 1
- reasoning: 一句话

规则：
1. 只使用上面的公式和干员；超出范围就 refuse 或 clarify。
2. 不要把技能倍率算进 atk_final；atk_final 是攻击力增益后的值。
3. 物理伤害需要 def_value；法术伤害需要 res_value。
4. 不要编造干员、技能或公式。"""
        data, _ = client.chat_json(prompt, max_tokens=900)
        if not data:
            return None
        try:
            plan = Plan.model_validate(data)
        except Exception:  # noqa: BLE001
            return None
        if plan.formula_id and plan.formula_id not in self.formula_by_id:
            plan.formula_id = None
            plan.confidence = min(plan.confidence, 0.4)
        return plan

    def _repair_plan(self, plan: Plan, query: str) -> Plan:
        """Re-derive variables from the curated catalog; never trust LLM math."""
        if not plan.formula_id or plan.formula_id not in self.formula_by_id:
            return plan
        if plan.intent == Intent.REFUSE:
            return plan
        card = self.formula_by_id[plan.formula_id]
        op, op_conf = self.resolve_operator(query)
        if op is None:
            for link in plan.entity_links:
                if link.kind == "operator":
                    op = next((o for o in self.catalog.operators if o.id == link.canonical_id), None)
                    if op is not None:
                        op_conf = link.confidence
                        break
        skill = self.resolve_skill(query, op)
        if skill is None:
            for link in plan.entity_links:
                if link.kind == "skill":
                    skill = next(
                        (
                            s
                            for o in self.catalog.operators
                            for s in o.skills
                            if s.id == link.canonical_id
                            and (op is None or s.operator_id == op.id)
                        ),
                        None,
                    )
                    if skill is not None:
                        break
        if skill and not skill.calc_supported and plan.intent in (
            Intent.CALCULATE,
            Intent.CLARIFY,
        ):
            plan.intent = Intent.REFUSE
            plan.refusal_reason = (
                f"{op.name if op else ''} 的「{skill.name}」暂不支持自动计算："
                f"{skill.unsupported_reason or '技能机制未纳入当前模型'}。"
            )
            return plan
        deterministic = self.extract_variables(query, plan.formula_id, op, skill)
        allowed = {v.symbol for v in card.variables}
        merged = dict(deterministic)
        for key, value in (plan.variable_bindings or {}).items():
            if key in allowed and key not in merged:
                try:
                    merged[key] = float(value)
                except (TypeError, ValueError):
                    continue

        required = [v.symbol for v in card.variables if v.required]
        missing = [name for name in required if name not in merged]
        plan.variable_bindings = merged
        numeric_keys = {
            "def_value", "res_value", "atk_final", "atk_base", "aspd_flat",
            "fragile", "damage_reduction", "base_damage", "damage_per_hit",
            "heal_multiplier", "healing_effect", "hits", "atk_time_base",
            "atk_time_flat", "atk_multiplier", "atk_pct", "interval",
        }
        has_numeric = any(k in merged for k in numeric_keys)

        if plan.intent in (Intent.CALCULATE, Intent.CLARIFY):
            if missing:
                plan.intent = Intent.CLARIFY
                plan.missing_vars = missing
            elif has_numeric:
                plan.intent = Intent.CALCULATE
                plan.missing_vars = []
            else:
                plan.intent = Intent.FORMULA_LOOKUP
                plan.missing_vars = []
        elif plan.intent == Intent.FORMULA_LOOKUP:
            plan.missing_vars = []
        return plan

    def plan(
        self, query: str, retrieved: list[RetrievedDoc] | None = None, use_llm: bool = True
    ) -> Plan:
        rule = self.rule_plan(query)
        if not use_llm or not client.enabled:
            return rule
        # Ask the LLM only when the rule planner is uncertain.
        if rule.confidence >= 0.8 and rule.intent != Intent.REFUSE:
            return rule
        llm = self.llm_plan(query, retrieved or [])
        if llm is None:
            return rule
        llm = self._repair_plan(llm, query)
        # The curated rule planner is more reliable for questions it understands;
        # do not let the LLM swap in a different formula for those.
        if (
            rule.formula_id
            and llm.formula_id
            and llm.formula_id != rule.formula_id
            and rule.confidence >= 0.7
        ):
            return rule
        if llm.confidence >= rule.confidence:
            return llm
        return rule
