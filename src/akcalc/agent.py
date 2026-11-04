"""Agent orchestration: retrieve -> plan -> calculate -> explain -> audit."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from .calculator import append_damage_components, evaluate_card
from .config import settings
from .llm import client
from .planner import Catalog, Planner, load_catalog
from .retrieval import Retriever
from .schemas import (
    Answer,
    CalculationResult,
    Citation,
    Intent,
    Plan,
    RetrievedDoc,
)

GROUNDED_SYSTEM_PROMPT = (
    "你是《明日方舟》数值助手。回答必须基于给定资料，给出计算过程和最终数值；"
    "资料不足时明确说不知道，不要编造公式或干员。"
)
RAW_SYSTEM_PROMPT = (
    "你是《明日方舟》数值助手。请使用你已有的游戏知识回答，写出所用公式、"
    "代入过程和最终数值；用中文回答。"
)


class Agent:
    def __init__(
        self,
        catalog: Catalog | None = None,
        retriever: Retriever | None = None,
        planner: Planner | None = None,
    ):
        self.catalog = catalog or load_catalog()
        self.retriever = retriever or Retriever()
        self.planner = planner or Planner(self.catalog)

    # ---------- public API ----------

    def ask(self, query: str, system: str = "full", top_k: int = 8) -> Answer:
        t0 = time.perf_counter()
        trace_id = uuid.uuid4().hex[:12]
        retrieved = self.retriever.search(query, top_k=top_k)

        if system == "raw":
            answer = self._answer_raw(query, retrieved, use_context=False, trace_id=trace_id)
        elif system == "rag":
            answer = self._answer_raw(query, retrieved, use_context=True, trace_id=trace_id)
        else:
            answer = self._answer_full(query, retrieved, trace_id=trace_id)

        answer.latency_ms = (time.perf_counter() - t0) * 1000
        self._audit(answer, retrieved)
        return answer

    # ---------- baselines ----------

    def _answer_raw(
        self, query: str, retrieved: list[RetrievedDoc], use_context: bool, trace_id: str
    ) -> Answer:
        context = ""
        if use_context:
            context = "\n\n参考资料：\n" + "\n".join(
                f"[{d.title}] {d.text[:800]}" for d in retrieved[:8]
            )
        prompt = f"问题：{query}\n{context}\n\n"
        if use_context:
            prompt += "请用中文回答，写出所用公式、代入过程和最终数值。若资料不足，请直接说明。"
            system = GROUNDED_SYSTEM_PROMPT
        else:
            prompt += "请用中文回答，写出所用公式、代入过程和最终数值。"
            system = RAW_SYSTEM_PROMPT
        result = client.chat(prompt, system=system, max_tokens=1200)
        plan = Plan(intent=Intent.CALCULATE, confidence=0.0, reasoning=f"{'rag' if use_context else 'raw'} baseline")
        return Answer(
            query=query,
            answer_text=result.text or f"[模型调用失败] {result.error}",
            plan=plan,
            refused=False,
            citations=[],
            confidence=0.0,
            trace_id=trace_id,
            token_usage=result.usage,
        )

    # ---------- full system ----------

    def _answer_full(self, query: str, retrieved: list[RetrievedDoc], trace_id: str) -> Answer:
        plan = self.planner.plan(query, retrieved=retrieved, use_llm=True)
        usage: dict[str, int] = {}

        if plan.intent == Intent.REFUSE:
            return self._refusal(query, plan, trace_id, usage)
        if plan.intent == Intent.CLARIFY:
            return self._clarification(query, plan, trace_id, usage)
        if plan.intent == Intent.ENTITY_LOOKUP:
            return self._entity_lookup(query, plan, trace_id, usage)

        card = self.planner.formula_by_id.get(plan.formula_id or "")
        if card is None:
            plan.intent = Intent.REFUSE
            plan.refusal_reason = "未找到可执行的公式。"
            return self._refusal(query, plan, trace_id, usage)

        if plan.intent == Intent.FORMULA_LOOKUP:
            return self._formula_lookup(query, plan, card, retrieved, trace_id, usage)

        # calculate
        try:
            result = evaluate_card(card, plan.variable_bindings)
        except Exception as exc:  # noqa: BLE001
            plan.intent = Intent.CLARIFY
            plan.missing_vars = [str(exc)]
            return self._clarification(query, plan, trace_id, usage)

        op, _ = self.planner.resolve_operator(query)
        skill = self.planner.resolve_skill(query, op)
        if (
            skill
            and skill.extra_components
            and card.id in {"physical_damage", "arts_damage", "true_damage"}
        ):
            result = append_damage_components(result, skill.extra_components, plan.variable_bindings)
        if skill and skill.calc_note:
            result.warnings.append(f"计算口径：{skill.calc_note}")

        # Apply fragile / damage reduction as a post-processing chain when present.
        fragile = float(plan.variable_bindings.get("fragile", 0.0) or 0.0)
        reduction = float(plan.variable_bindings.get("damage_reduction", 0.0) or 0.0)
        if card.id in {"physical_damage", "arts_damage", "true_damage"} and (fragile or reduction):
            from .calculator import calc_final_damage

            mod = calc_final_damage(
                {"base_damage": result.raw_value, "fragile": fragile, "damage_reduction": reduction}
            )
            for step in mod.steps:
                step.note = (step.note + "（对已结算的防御/抗性伤害套用）").strip()
            result.steps.extend(mod.steps)
            result.raw_value = mod.raw_value
            result.display_value = mod.display_value
            result.warnings.extend(mod.warnings)

        text = self._calculation_text(query, plan, result)
        citations = self._merge_citations(result.citations, retrieved)
        return Answer(
            query=query,
            answer_text=text,
            plan=plan,
            calculation=result,
            citations=citations,
            refused=False,
            confidence=plan.confidence,
            trace_id=trace_id,
            token_usage=usage,
        )

    def _entity_lookup(
        self, query: str, plan: Plan, trace_id: str, usage: dict
    ) -> Answer:
        op = None
        for link in plan.entity_links:
            if link.kind == "operator":
                op = next((o for o in self.catalog.operators if o.id == link.canonical_id), None)
                break
        if op is None:
            op, _ = self.planner.resolve_operator(query)
        if op is None:
            plan.intent = Intent.REFUSE
            plan.refusal_reason = "没有找到对应的干员。"
            return self._refusal(query, plan, trace_id, usage)

        lines = [
            f"【干员】{op.name}（{op.class_name}，{op.rarity}★，精英 {op.phase} 级 {op.level}）",
            f"生命 {op.hp:g}｜攻击 {op.atk:g}｜防御 {op.def_value:g}｜"
            f"法抗 {op.res_value:.0%}｜攻击间隔 {op.attack_interval:g}s",
        ]
        if op.trait:
            lines.append(f"特性：{op.trait}")
        if op.skills:
            lines.extend(["", "【技能】"])
            for sk in op.skills:
                tag = "可计算" if sk.calc_supported else f"暂不支持：{sk.unsupported_reason}"
                extra = ""
                if sk.calc_supported:
                    extra = (
                        f"（{sk.damage_type}；攻击力加成 {sk.atk_bonus:+.0%}；"
                        f"倍率 {sk.atk_multiplier:g}；{sk.hits} 段"
                        + (f"；口径：{sk.calc_note}" if sk.calc_note else "")
                        + "）"
                    )
                lines.append(f"{sk.skill_index}. {sk.name}{extra} — {tag}")
        citations = [op.citation] if op.citation else []
        lines.extend(["", f"【出处】{op.citation.title if op.citation else '游戏数据'}"])
        return Answer(
            query=query,
            answer_text="\n".join(lines),
            plan=plan,
            citations=citations,
            confidence=plan.confidence,
            trace_id=trace_id,
            token_usage=usage,
        )

    def _formula_lookup(
        self,
        query: str,
        plan: Plan,
        card,
        retrieved: list[RetrievedDoc],
        trace_id: str,
        usage: dict,
    ) -> Answer:
        plan.missing_vars = []
        related = [
            d.title for d in retrieved if d.kind == "formula" and d.doc_id != f"formula:{card.id}"
        ][:3]
        lines = [
            f"【公式】{card.name}",
            f"表达式：{card.expression}",
            "",
            "【变量】",
        ]
        for v in card.variables:
            req = "必需" if v.required else "可选"
            unit = f"，单位：{v.unit}" if v.unit else ""
            lines.append(f"- {v.symbol}（{v.name}，{req}{unit}）：{v.description}")
        if card.assumptions:
            lines.extend(["", "【适用条件】"] + [f"- {a}" for a in card.assumptions])
        if card.notes:
            lines.extend(["", f"【说明】{card.notes}"])
        if related:
            lines.extend(["", "【相关公式】" + "；".join(related)])
        lines.extend(["", f"【出处】{card.citation.title} — {card.citation.section}", card.citation.url])
        return Answer(
            query=query,
            answer_text="\n".join(lines),
            plan=plan,
            citations=[card.citation],
            confidence=plan.confidence,
            trace_id=trace_id,
            token_usage=usage,
        )

    def _calculation_text(self, query: str, plan: Plan, result: CalculationResult) -> str:
        lines = [f"【结论】{result.formula_name} = {result.display_value:g} {result.unit}".rstrip()]
        if abs(result.display_value - result.raw_value) > 1e-9:
            lines.append(f"（未取整值：{result.raw_value:g} {result.unit}）")
        lines.extend(["", f"【公式】{result.expression}"])
        lines.extend(["", "【代入与步骤】"])
        for i, step in enumerate(result.steps, 1):
            value = f" = {step.value:g}" if isinstance(step.value, (int, float)) else ""
            lines.append(f"{i}. {step.label}：{step.expression}{value}")
            if step.note:
                lines.append(f"   注：{step.note}")
        if result.warnings:
            lines.extend(["", "【警告】"] + [f"- {w}" for w in result.warnings])
        if result.citations:
            lines.extend(["", "【出处】"])
            for c in result.citations:
                lines.append(f"- {c.title} — {c.section}: {c.url}")
        return "\n".join(lines)

    def _refusal(self, query: str, plan: Plan, trace_id: str, usage: dict) -> Answer:
        text = plan.refusal_reason or "这个问题超出当前知识库范围，暂时无法回答。"
        return Answer(
            query=query,
            answer_text=f"【无法回答】{text}",
            plan=plan,
            refused=True,
            refusal_reason=text,
            confidence=plan.confidence,
            trace_id=trace_id,
            token_usage=usage,
        )

    def _clarification(self, query: str, plan: Plan, trace_id: str, usage: dict) -> Answer:
        labels: dict[str, str] = {}
        card = self.planner.formula_by_id.get(plan.formula_id or "")
        if card:
            labels = {v.symbol: v.name for v in card.variables}
        if plan.missing_vars:
            parts = []
            for m in plan.missing_vars:
                if m in labels:
                    parts.append(f"{labels[m]}（{m}）")
                else:
                    parts.append(m)
            missing = "、".join(parts)
        else:
            missing = "关键信息"
        text = f"需要补充：{missing}。请给出具体数值或明确公式类型后我再计算。"
        return Answer(
            query=query,
            answer_text=f"【需要澄清】{text}",
            plan=plan,
            refused=False,
            confidence=plan.confidence,
            trace_id=trace_id,
            token_usage=usage,
        )

    # ---------- helpers ----------

    @staticmethod
    def _merge_citations(citations: list[Citation], retrieved: list[RetrievedDoc]) -> list[Citation]:
        out: list[Citation] = []
        seen: set[str] = set()
        for c in citations:
            if c.source_id not in seen:
                out.append(c)
                seen.add(c.source_id)
        for d in retrieved:
            if d.kind != "formula":
                continue
            payload = d.payload or {}
            cit = payload.get("citation")
            if cit and cit.get("source_id") not in seen:
                out.append(Citation.model_validate(cit))
                seen.add(cit["source_id"])
        return out

    def _audit(self, answer: Answer, retrieved: list[RetrievedDoc]) -> None:
        settings.ensure_dirs()
        path = settings.log_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": answer.trace_id,
            "query": answer.query,
            "intent": answer.plan.intent.value,
            "formula_id": answer.plan.formula_id,
            "variables": answer.plan.variable_bindings,
            "missing_vars": answer.plan.missing_vars,
            "refused": answer.refused,
            "answer": answer.answer_text,
            "citations": [c.source_id for c in answer.citations],
            "retrieved": [d.doc_id for d in retrieved],
            "latency_ms": answer.latency_ms,
            "tokens": answer.token_usage,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
