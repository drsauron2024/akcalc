"""Optional Semantica integration.

Semantica is the course's knowledge-infrastructure layer. We use it for:
  - ingest/parse of source snapshots (FileIngestor + DocumentParser)
  - graph construction from the curated ontology (GraphBuilder)
  - provenance tracking for formula/relationship sources (ProvenanceManager)

The core agent does not depend on Semantica; if the package is unavailable the
adapter raises a clear error and the rest of the system still runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

KB_DIR = PROJECT_ROOT / "data" / "kb"


def semantica_available() -> bool:
    try:
        import semantica  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def parse_with_semantica(path: str | Path) -> dict[str, Any]:
    """Parse a local HTML/PDF/DOCX source and return text + metadata."""
    from semantica.ingest import FileIngestor
    from semantica.parse import DocumentParser

    sources = FileIngestor().ingest(str(path))
    if not sources:
        raise RuntimeError(f"Semantica could not ingest {path}")
    parsed = DocumentParser().parse(sources[0].path)
    if isinstance(parsed, dict):
        text = parsed.get("full_text", "")
        metadata = parsed.get("metadata", {})
    else:
        text = getattr(parsed, "text", "")
        metadata = getattr(parsed, "metadata", {})
    return {
        "path": str(path),
        "text": text,
        "metadata": metadata,
        "text_length": len(text),
    }


def build_semantica_graph(
    graph_path: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Rebuild our ontology with Semantica's GraphBuilder and persist it."""
    from semantica.kg import GraphBuilder

    graph_path = graph_path or (KB_DIR / "graph.json")
    out_path = out_path or (KB_DIR / "semantica_graph.json")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    builder = GraphBuilder(merge_entities=False)
    result = builder.build(
        {"entities": graph["nodes"], "relationships": graph["edges"]}
    )
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return result


def build_provenance(
    graph_path: Path | None = None,
    storage_path: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Track every formula/relationship back to its source and export PROV."""
    from semantica.provenance import ProvenanceManager

    graph_path = graph_path or (KB_DIR / "graph.json")
    storage_path = storage_path or (KB_DIR / "provenance.db")
    out_path = out_path or (KB_DIR / "provenance.ttl")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    # Citations live in formula payloads, not in graph.json; read them from YAML.
    import yaml

    formulas = yaml.safe_load(
        (PROJECT_ROOT / "data" / "curation" / "formulas.yaml").read_text(encoding="utf-8")
    )["formulas"]
    citation_by_formula = {
        f"formula:{f['id']}": f.get("citation", {}) for f in formulas
    }

    pm = ProvenanceManager(storage_path=str(storage_path))
    tracked = 0
    for node in graph["nodes"]:
        if node["kind"] != "formula":
            continue
        cit = citation_by_formula.get(node["id"], {})
        pm.track_entity(
            node["id"],
            source=cit.get("url", ""),
            metadata={
                "title": cit.get("title", ""),
                "section": cit.get("section", ""),
                "retrieved_at": cit.get("retrieved_at", ""),
            },
        )
        tracked += 1
    for edge in graph["edges"]:
        pm.track_relationship(
            f"{edge['source']}--{edge['relation']}--{edge['target']}",
            source="curated ontology",
            metadata={"relation": edge["relation"]},
        )
    try:
        prov = pm.export_prov(format="turtle")
    except Exception:  # noqa: BLE001
        prov = json.dumps(pm.get_statistics(), ensure_ascii=False)
    out_path.write_text(prov, encoding="utf-8")
    return {"tracked_entities": tracked, "statistics": pm.get_statistics(), "path": str(out_path)}
