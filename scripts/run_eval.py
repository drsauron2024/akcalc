"""Run the 20-question benchmark.

Usage:
    python scripts/run_eval.py --system full
    python scripts/run_eval.py --system raw --limit 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from akcalc.eval import run_eval  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", choices=["full", "rag", "raw"], default="full")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    records, summary, path = run_eval(args.system, limit=args.limit)
    print("\n" + "=" * 60)
    print(f"system={summary.system} n={summary.n}")
    print(f"numeric_accuracy   = {summary.numeric_accuracy:.3f}")
    print(f"formula_accuracy   = {summary.formula_accuracy:.3f}")
    print(f"citation_accuracy  = {summary.citation_accuracy:.3f}")
    print(f"refusal_accuracy   = {summary.refusal_accuracy:.3f}")
    print(f"clarify_accuracy   = {summary.clarify_accuracy:.3f}")
    print(f"hallucination_rate = {summary.hallucination_rate:.3f}")
    print(f"avg_latency_ms     = {summary.avg_latency_ms:.0f}")
    print(f"saved: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
