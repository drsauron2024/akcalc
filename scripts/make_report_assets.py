"""Aggregate the latest full/rag/raw eval runs into CSV + Markdown report tables."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from akcalc.eval import load_questions, summarize  # noqa: E402
from akcalc.schemas import EvalRecord  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = ROOT / "results"


def latest_by_system() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for f in sorted(RESULTS.glob("eval_*.json")):
        system = f.name.split("_")[1]
        if system not in out or f.stat().st_mtime > out[system].stat().st_mtime:
            out[system] = f
    return out


def main() -> int:
    questions = {q.id: q for q in load_questions()}
    latest = latest_by_system()
    if not latest:
        print("no eval results found; run scripts/run_eval.py first")
        return 2

    summary_rows = []
    record_rows = []
    for system, path in latest.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        records = [EvalRecord.model_validate(r) for r in data["records"]]
        summary = summarize(system, records, questions).model_dump()
        # Keep the saved JSON consistent with the recomputed (NaN-aware) summary.
        data["summary"] = summary
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary["file"] = path.name
        summary_rows.append(summary)
        for r in data["records"]:
            q = questions[r["question_id"]]
            r["category"] = q.category
            r["system"] = system
            record_rows.append(r)

    sdf = pd.DataFrame(summary_rows)[
        [
            "system",
            "numeric_accuracy",
            "formula_accuracy",
            "citation_accuracy",
            "refusal_accuracy",
            "clarify_accuracy",
            "hallucination_rate",
            "avg_latency_ms",
            "n",
            "file",
        ]
    ]
    sdf.to_csv(RESULTS / "summary.csv", index=False, encoding="utf-8-sig")

    rdf = pd.DataFrame(record_rows)
    rdf.to_csv(RESULTS / "per_question.csv", index=False, encoding="utf-8-sig")

    # Per-category numeric / refusal / clarify accuracy.
    cat_rows = []
    for (system, category), g in rdf.groupby(["system", "category"]):
        numeric = g["numeric_correct"].dropna()
        refusal = g["refusal_correct"].dropna()
        clarify = g["clarify_correct"].dropna()
        cat_rows.append(
            {
                "system": system,
                "category": category,
                "n": len(g),
                "numeric_acc": numeric.mean() if len(numeric) else None,
                "refusal_acc": refusal.mean() if len(refusal) else None,
                "clarify_acc": clarify.mean() if len(clarify) else None,
            }
        )
    cdf = pd.DataFrame(cat_rows).sort_values(["category", "system"])
    cdf.to_csv(RESULTS / "by_category.csv", index=False, encoding="utf-8-sig")

    lines = ["# 评测结果汇总（自动生成）", ""]
    lines.append("## 总体")
    lines.append("")
    sdf_display = sdf.astype(object).where(sdf.notna(), "N/A")
    cdf_display = cdf.astype(object).where(cdf.notna(), "N/A")
    lines.append(sdf_display.to_markdown(index=False, floatfmt=".3f"))
    lines.append("")
    lines.append("## 分类别")
    lines.append("")
    lines.append(cdf_display.to_markdown(index=False, floatfmt=".3f"))
    lines.append("")
    lines.append("## 数据来源")
    for system, path in latest.items():
        lines.append(f"- {system}: `{path.name}`")
    (RESULTS / "report_tables.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {RESULTS / 'summary.csv'}")
    print(f"wrote {RESULTS / 'per_question.csv'}")
    print(f"wrote {RESULTS / 'by_category.csv'}")
    print(f"wrote {RESULTS / 'report_tables.md'}")
    print(sdf.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
