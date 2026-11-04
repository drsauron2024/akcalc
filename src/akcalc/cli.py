"""Minimal CLI: python -m akcalc.cli ask "银灰三技能对防御500的伤害？"."""

from __future__ import annotations

import argparse
import sys

from .agent import Agent


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="akcalc")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--system", choices=["full", "rag", "raw"], default="full")
    ask.add_argument("--json", action="store_true", help="print the full Answer as JSON")
    args = parser.parse_args(argv)

    agent = Agent()
    question = " ".join(args.question)
    answer = agent.ask(question, system=args.system)
    if args.json:
        print(answer.model_dump_json(indent=2))
    else:
        print(answer.answer_text)
        print(f"\n[intent={answer.plan.intent.value} formula={answer.plan.formula_id} "
              f"confidence={answer.confidence:.2f} latency={answer.latency_ms:.0f}ms]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
