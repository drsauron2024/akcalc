"""Build SQLite KB, retrieval corpus and ontology graph from curated YAML."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from akcalc.schemas import FormulaCard, OperatorRecord  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CURATION = ROOT / "data" / "curation"
KB_DIR = ROOT / "data" / "kb"
DB_PATH = KB_DIR / "akcalc.db"
GRAPH_PATH = KB_DIR / "graph.json"
CORPUS_PATH = KB_DIR / "corpus.jsonl"

FORMULA_BY_DAMAGE = {
    "physical": "physical_damage",
    "arts": "arts_damage",
    "true": "true_damage",
    "healing": "healing",
}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def formula_text(card: FormulaCard) -> str:
    variables = "；".join(
        f"{v.symbol}={v.name}" + (f"（{v.unit}）" if v.unit else "") for v in card.variables
    )
    return "\n".join(
        [
            f"公式：{card.name}",
            "别名：" + "、".join(card.aliases),
            f"表达式：{card.expression}",
            f"变量：{variables}",
            "假设：" + "；".join(card.assumptions),
            f"说明：{card.notes}",
        ]
    )


def _extra_text(skill) -> str:
    if not skill.extra_components:
        return ""
    return "；附加：" + "、".join(
        f"{c.label} {c.damage_type} {c.atk_multiplier:.0%}×{c.hits}段"
        for c in skill.extra_components
    )


def operator_text(op: OperatorRecord) -> str:
    skills = "；".join(
        f"{s.name}（{s.damage_type}，攻击力加成 {s.atk_bonus:+.0%}，倍率 {s.atk_multiplier:g}，"
        f"{s.hits} 段，间隔修正 {s.atk_time_flat:+g}s，"
        f"{'可计算' if s.calc_supported else '暂不支持计算'}{_extra_text(s)}）"
        for s in op.skills
    )
    return (
        f"干员：{op.name}；职业：{op.class_name}；子职业：{op.sub_profession}；稀有度：{op.rarity}；"
        f"精英 {op.phase} 级 {op.level}；攻击力 {op.atk:g}；防御 {op.def_value:g}；"
        f"法抗 {op.res_value:.0%}；生命 {op.hp:g}；攻击间隔 {op.attack_interval:g}s。"
        f"特性：{op.trait}。技能：{skills}"
    )


def skill_text(op: OperatorRecord, skill) -> str:
    return (
        f"干员 {op.name} 的技能「{skill.name}」（第 {skill.skill_index} 技能，"
        f"数值取 M3/最高等级）：{skill.description}。伤害类型 {skill.damage_type}；攻击力加成 {skill.atk_bonus:+.0%}；"
        f"技能倍率 {skill.atk_multiplier:g}；治疗倍率 {skill.heal_multiplier:g}；"
        f"段数 {skill.hits}；攻击间隔修正 {skill.atk_time_flat:+g}s；ASPD {skill.aspd_flat:+g}。"
        f"{'可计算' if skill.calc_supported else '暂不支持计算：' + skill.unsupported_reason}"
        + _extra_text(skill)
        + (f"；计算口径：{skill.calc_note}" if skill.calc_note else "")
        + (f"；参数化公式：{skill.formula_note}" if skill.formula_note else "")
    )


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS formulas;
        DROP TABLE IF EXISTS operators;
        DROP TABLE IF EXISTS skills;
        DROP TABLE IF EXISTS meta;
        CREATE TABLE formulas (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            damage_type TEXT,
            expression TEXT,
            aliases TEXT,
            variables TEXT,
            assumptions TEXT,
            notes TEXT,
            citation TEXT,
            examples TEXT,
            text TEXT
        );
        CREATE TABLE operators (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            class_name TEXT,
            rarity INTEGER,
            phase INTEGER,
            level INTEGER,
            atk REAL,
            def_value REAL,
            res_value REAL,
            hp REAL,
            attack_interval REAL,
            text TEXT
        );
        CREATE TABLE skills (
            uid TEXT PRIMARY KEY,
            id TEXT,
            operator_id TEXT,
            name TEXT,
            skill_index INTEGER,
            description TEXT,
            atk_bonus REAL,
            atk_multiplier REAL,
            heal_multiplier REAL,
            atk_time_flat REAL,
            aspd_flat REAL,
            damage_type TEXT,
            hits INTEGER,
            duration REAL,
            calc_supported INTEGER,
            unsupported_reason TEXT,
            calc_note TEXT,
            text TEXT,
            FOREIGN KEY(operator_id) REFERENCES operators(id)
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )


def main() -> int:
    formulas_raw = load_yaml(CURATION / "formulas.yaml")
    op_path = CURATION / "operators_all.yaml"
    if not op_path.exists():
        op_path = CURATION / "operators.yaml"
    operators_raw = load_yaml(op_path)

    formulas = [FormulaCard.model_validate(x) for x in formulas_raw["formulas"]]
    operators = [OperatorRecord.model_validate(x) for x in operators_raw["operators"]]

    # Fail fast on duplicate ids and dangling references.
    assert len({f.id for f in formulas}) == len(formulas), "duplicate formula id"
    assert len({o.id for o in operators}) == len(operators), "duplicate operator id"
    for op in operators:
        for sk in op.skills:
            assert sk.damage_type in FORMULA_BY_DAMAGE, f"bad damage type: {sk.damage_type}"

    KB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        for card in formulas:
            conn.execute(
                "INSERT INTO formulas VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card.id,
                    card.name,
                    card.damage_type,
                    card.expression,
                    json.dumps(card.aliases, ensure_ascii=False),
                    json.dumps([v.model_dump() for v in card.variables], ensure_ascii=False),
                    json.dumps(card.assumptions, ensure_ascii=False),
                    card.notes,
                    json.dumps(card.citation.model_dump(), ensure_ascii=False),
                    json.dumps(card.examples, ensure_ascii=False),
                    formula_text(card),
                ),
            )
        for op in operators:
            conn.execute(
                "INSERT INTO operators VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    op.id,
                    op.name,
                    op.class_name,
                    op.rarity,
                    op.phase,
                    op.level,
                    op.atk,
                    op.def_value,
                    op.res_value,
                    op.hp,
                    op.attack_interval,
                    operator_text(op),
                ),
            )
            for sk in op.skills:
                conn.execute(
                    "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{sk.operator_id}:{sk.id}",
                        sk.id,
                        sk.operator_id,
                        sk.name,
                        sk.skill_index,
                        sk.description,
                        sk.atk_bonus,
                        sk.atk_multiplier,
                        sk.heal_multiplier,
                        sk.atk_time_flat,
                        sk.aspd_flat,
                        sk.damage_type,
                        sk.hits,
                        sk.duration,
                        int(sk.calc_supported),
                        sk.unsupported_reason,
                        sk.calc_note,
                        skill_text(op, sk),
                    ),
                )
        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "formula_version": formulas_raw.get("version", ""),
            "operator_version": operators_raw.get("version", ""),
            "n_formulas": len(formulas),
            "n_operators": len(operators),
            "n_skills": sum(len(o.skills) for o in operators),
        }
        for k, v in meta.items():
            conn.execute("INSERT INTO meta VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

    # Retrieval corpus (JSONL).
    corpus: list[dict] = []
    for card in formulas:
        corpus.append(
            {
                "doc_id": f"formula:{card.id}",
                "kind": "formula",
                "title": card.name,
                "text": formula_text(card),
                "payload": card.model_dump(),
            }
        )
    for op in operators:
        corpus.append(
            {
                "doc_id": f"operator:{op.id}",
                "kind": "operator",
                "title": op.name,
                "text": operator_text(op),
                "payload": op.model_dump(),
            }
        )
        for sk in op.skills:
            corpus.append(
                {
                    "doc_id": f"skill:{op.id}:{sk.id}",
                    "kind": "skill",
                    "title": f"{op.name} · {sk.name}",
                    "text": skill_text(op, sk),
                    "payload": {**sk.model_dump(), "operator_name": op.name},
                }
            )
    with CORPUS_PATH.open("w", encoding="utf-8") as f:
        for doc in corpus:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    # Ontology-style graph.
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(node_id: str, label: str, kind: str, **extra) -> None:
        nodes[node_id] = {"id": node_id, "label": label, "kind": kind, **extra}

    def add_edge(src: str, dst: str, rel: str) -> None:
        edges.append({"source": src, "target": dst, "relation": rel})

    for card in formulas:
        add_node(f"formula:{card.id}", card.name, "formula", damage_type=card.damage_type)
        for var in card.variables:
            vid = f"variable:{var.symbol}"
            add_node(vid, f"{var.symbol}（{var.name}）", "variable", unit=var.unit)
            add_edge(f"formula:{card.id}", vid, "requires")
    for op in operators:
        add_node(
            f"operator:{op.id}",
            op.name,
            "operator",
            atk=op.atk,
            supported_skills=sum(1 for s in op.skills if s.calc_supported),
        )
        for sk in op.skills:
            skill_node = f"skill:{op.id}:{sk.id}"
            add_node(
                skill_node,
                sk.name,
                "skill",
                damage_type=sk.damage_type,
                calc_supported=sk.calc_supported,
            )
            add_edge(f"operator:{op.id}", skill_node, "has_skill")
            if sk.calc_supported:
                target = FORMULA_BY_DAMAGE[sk.damage_type]
                add_edge(skill_node, f"formula:{target}", "uses_formula")
                if sk.atk_bonus != 0:
                    add_edge(skill_node, "formula:atk_final", "uses_formula")
                for comp in sk.extra_components:
                    add_edge(skill_node, f"formula:{FORMULA_BY_DAMAGE[comp.damage_type]}", "uses_formula")
    graph = {"nodes": list(nodes.values()), "edges": edges}
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"KB: {DB_PATH}")
    print(f"corpus: {CORPUS_PATH} ({len(corpus)} docs)")
    print(f"graph: {GRAPH_PATH} ({len(nodes)} nodes, {len(edges)} edges)")
    print(f"formulas={len(formulas)} operators={len(operators)} skills={sum(len(o.skills) for o in operators)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
