"""Use Semantica to parse a source, rebuild the ontology graph, and export PROV."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from akcalc.semantica_adapter import (  # noqa: E402
    build_provenance,
    build_semantica_graph,
    parse_with_semantica,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    sample = ROOT / "data" / "raw" / "wiki" / "wiki_gg_damage_Physical.html"
    if not sample.exists():
        print(f"missing sample: {sample}")
        return 2

    parsed = parse_with_semantica(sample)
    out = ROOT / "data" / "kb" / "semantica_parse_sample.json"
    out.write_text(
        json.dumps(
            {
                "path": parsed["path"],
                "text_length": parsed["text_length"],
                "metadata": parsed["metadata"],
                "text_preview": parsed["text"][:3000],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"[parse] {out} ({parsed['text_length']} chars)")

    graph = build_semantica_graph()
    print(
        f"[graph] entities={len(graph.get('entities', []))} "
        f"relationships={len(graph.get('relationships', []))} -> data/kb/semantica_graph.json"
    )

    prov = build_provenance()
    print(f"[provenance] tracked={prov['tracked_entities']} -> {prov['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
