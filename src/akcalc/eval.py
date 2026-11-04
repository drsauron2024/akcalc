"""Evaluation runner: raw LLM vs naive RAG vs full ontology agent."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .agent import Agent
from .config import PROJECT_ROOT
from .schemas import EvalQuestion, EvalRecord, EvalSummary, Intent

QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.jsonl"
RESULTS_DIR = PROJECT_ROOT / "results"

REFUSAL_MARKERS = [
    "无法回答",
    "无法计算",
    "无法可靠",
    "无法给出",
    "无法判断",
    "无法确定",
    "不能回答",
    "不能保证",
    "不属于",
    "不在",
    "超出",
    "不知道",
    "没有相关",
    "仅覆盖",
    "只覆盖",
    "无法用",
    "不能用公式",
    "无法查到",
    "无法获取",
]
CLARIFY_MARKERS = [
    "需要补充",
    "请补充",
    "请提供",
    "缺少",
    "请给出",
    "需要知道",
    "请明确",
    "需要以下",
    "不能给出唯一",
    "无法唯一",
    "取决于",
    "不知道你要",
    "不知道你",
    "无法给出对具体",
]


def load_questions(path: Path = QUESTIONS_PATH) -> list[EvalQuestion]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(EvalQuestion.model_validate(json.loads(line)))
    return out


def extract_number(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(",", "")
    # Remove version/skill markers that are not answers ("M3", "S3", "精二", "第3技能").
    text = re.sub(r"\b[MmSs]\d+\b", " ", text)
    text = re.sub(r"精[一二三四五六七八九十]", " ", text)
    text = re.sub(r"第?\s*[123]\s*技能", " ", text)
    patterns = [
        r"\\boxed\{\s*(-?\d+(?:\.\d+)?)",
        r"(?:最终结果|最终数值|最终答案|演示结果)\s*[：:为]?\s*(?:约\s*)?(-?\d+(?:\.\d+)?)",
        r"(?:最终单次|最终)(?:物理|法术|真实)?伤害[：:为]\s*(-?\d+(?:\.\d+)?)",
        r"\*\*[^*\n]{0,30}?(-?\d+(?:\.\d+)?)\s*(?:点|秒)?\s*\*\*",
        r"(?:约为|总伤害为|最终伤害为|单次(?:物理|法术|真实)?伤害为|最终攻击力为|结果为|得到)\s*[^\d\-]{0,12}(-?\d+(?:\.\d+)?)",
        r"(?:为|是)\s*[^\d\-]{0,4}(-?\d+(?:\.\d+)?)\s*(?:点|秒)?\s*\*\*\s*$",
        r"\*\*\s*(-?\d+(?:\.\d+)?)\s*(?:点|秒)?\s*\*\*",
        r"(?:答案|结论|所以|因此)[^\d\-]{0,80}(-?\d+(?:\.\d+)?)",
        r"=\s*(-?\d+(?:\.\d+)?)\s*(?:\\?\\?text\{(?:点|秒)\}|点|秒|$)",
        r"(-?\d+(?:\.\d+)?)\s*(?:\\?\\?text\{(?:点|秒)\}|点|秒)",
    ]
    for pat in patterns:
        found = re.findall(pat, text)
        if found:
            try:
                return float(found[-1])
            except ValueError:
                continue
    numbers = re.findall(r"(?<![\d.])(-?\d+(?:\.\d+)?)(?!\s*%)", text)
    if numbers:
        try:
            return float(numbers[-1])
        except ValueError:
            return None
    return None


def _looks_refused(text: str) -> bool:
    # A cross-game answer that still prints a full formula is not a refusal,
    # even if it contains caveats such as "无法给出唯一数值".
    if "原神" in text and any(k in text for k in ["ATK_{E}", "蝶引来生"]):
        return False
    return any(marker in text for marker in REFUSAL_MARKERS)


def _looks_clarify(text: str) -> bool:
    return any(marker in text for marker in CLARIFY_MARKERS)


def score_answer(q: EvalQuestion, system: str, answer) -> EvalRecord:
    text = answer.answer_text or ""
    pred_value = answer.calculation.display_value if answer.calculation else None
    if system in {"raw", "rag"}:
        pred_value = extract_number(text)

    refused = answer.refused if system == "full" else _looks_refused(text)
    clarify = (
        answer.plan.intent == Intent.CLARIFY if system == "full" else _looks_clarify(text)
    )

    numeric_correct = None
    if q.gold_value is not None and pred_value is not None:
        numeric_correct = abs(pred_value - q.gold_value) <= q.tolerance
    elif q.gold_value is not None:
        numeric_correct = False

    formula_correct = None
    citation_correct = None
    if system == "full":
        if q.formula_ids:
            formula_correct = answer.plan.formula_id in q.formula_ids
        if q.citation_ids:
            citation_correct = any(c.source_id in q.citation_ids for c in answer.citations)

    refusal_correct = None
    if q.refusal_expected:
        refusal_correct = refused
    clarify_correct = None
    if q.clarify_expected:
        clarify_correct = clarify

    hallucination = bool(q.refusal_expected and not refused)

    return EvalRecord(
        question_id=q.id,
        system=system,
        prediction=text,
        predicted_value=pred_value,
        gold_value=q.gold_value,
        numeric_correct=numeric_correct,
        formula_correct=formula_correct,
        citation_correct=citation_correct,
        refusal_correct=refusal_correct,
        clarify_correct=clarify_correct,
        hallucination=hallucination,
        latency_ms=answer.latency_ms,
        error="",
    )


def summarize(system: str, records: list[EvalRecord], questions: dict[str, EvalQuestion]) -> EvalSummary:
    def acc(values: list[bool]) -> float:
        return sum(values) / len(values) if values else float("nan")

    numeric = [r.numeric_correct for r in records if r.numeric_correct is not None]
    formula = [r.formula_correct for r in records if r.formula_correct is not None]
    citation = [r.citation_correct for r in records if r.citation_correct is not None]
    refusal = [r.refusal_correct for r in records if r.refusal_correct is not None]
    clarify = [r.clarify_correct for r in records if r.clarify_correct is not None]
    halluc = [r.hallucination for r in records if questions[r.question_id].refusal_expected]
    latencies = [r.latency_ms for r in records]

    return EvalSummary(
        system=system,
        n=len(records),
        numeric_accuracy=acc([bool(x) for x in numeric]),
        formula_accuracy=acc([bool(x) for x in formula]),
        citation_accuracy=acc([bool(x) for x in citation]),
        refusal_accuracy=acc([bool(x) for x in refusal]),
        clarify_accuracy=acc([bool(x) for x in clarify]),
        hallucination_rate=acc([bool(x) for x in halluc]),
        avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
    )


def run_eval(
    system: str,
    limit: int | None = None,
    questions_path: Path = QUESTIONS_PATH,
) -> tuple[list[EvalRecord], EvalSummary, Path]:
    questions = load_questions(questions_path)
    if limit:
        questions = questions[:limit]
    qmap = {q.id: q for q in questions}
    agent = Agent()
    records: list[EvalRecord] = []
    for i, q in enumerate(questions, 1):
        print(f"[{system}] {i}/{len(questions)} {q.id} ...", flush=True)
        try:
            answer = agent.ask(q.question, system=system)
            record = score_answer(q, system, answer)
        except Exception as exc:  # noqa: BLE001
            record = EvalRecord(
                question_id=q.id,
                system=system,
                prediction="",
                gold_value=q.gold_value,
                error=f"{type(exc).__name__}: {exc}",
            )
        records.append(record)
        status = "?"
        if record.numeric_correct is not None:
            status = "OK" if record.numeric_correct else "WRONG"
        elif record.refusal_correct is not None:
            status = "OK" if record.refusal_correct else "WRONG"
        elif record.clarify_correct is not None:
            status = "OK" if record.clarify_correct else "WRONG"
        if record.error:
            status = "ERROR"
        print(
            f"    {status} pred={record.predicted_value} gold={record.gold_value} "
            f"({record.latency_ms:.0f} ms)",
            flush=True,
        )

    summary = summarize(system, records, qmap)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"eval_{system}_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "system": system,
                "run_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary.model_dump(),
                "records": [r.model_dump() for r in records],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return records, summary, out
