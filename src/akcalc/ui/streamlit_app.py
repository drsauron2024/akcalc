"""Streamlit demo: ask, formula library, ontology graph, eval dashboard, audit log."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from akcalc.agent import Agent  # noqa: E402
from akcalc.config import PROJECT_ROOT, settings  # noqa: E402
from akcalc.planner import load_catalog  # noqa: E402

st.set_page_config(page_title="明日方舟数值助手", page_icon="🎯", layout="wide")

EXAMPLES = [
    "银灰三技能对防御 500 的敌人单次伤害是多少？",
    "银灰三技能对防御 2100 的敌人单次伤害是多少？",
    "艾雅法拉三技能对法抗 30% 的敌人伤害是多少？",
    "能天使三技能攻击间隔是多少？",
    "能天使三技能对防御 600 的敌人一次五连射总伤害是多少？",
    "银灰三技能攻击力是多少？",
    "史尔特尔三技能对法抗 30% 的敌人伤害是多少？",
    "令的面板攻击力是多少？",
    "物理伤害怎么算？",
    "银灰的皮肤什么时候复刻？",
]


@st.cache_resource(show_spinner="正在加载知识库与检索索引...")
def get_agent() -> Agent:
    return Agent()


def render_answer(answer) -> None:
    st.markdown(answer.answer_text)
    cols = st.columns(4)
    cols[0].metric("意图", answer.plan.intent.value)
    cols[1].metric("公式", answer.plan.formula_id or "—")
    cols[2].metric("置信度", f"{answer.confidence:.2f}")
    cols[3].metric("耗时", f"{answer.latency_ms:.0f} ms")
    if answer.citations:
        st.caption("引用：" + "；".join(f"{c.title}（{c.section}）" for c in answer.citations))
    with st.expander("查看结构化 trace（审计记录）"):
        st.json(
            {
                "trace_id": answer.trace_id,
                "plan": answer.plan.model_dump(),
                "calculation": answer.calculation.model_dump() if answer.calculation else None,
                "token_usage": answer.token_usage,
            }
        )


def page_ask() -> None:
    st.subheader("数值问答")
    st.caption("完整系统会先检索公式/干员，再由确定性计算器结算；模型只负责理解与解释。")
    mode = st.radio(
        "对比模式",
        ["full（本系统）", "rag（朴素检索增强）", "raw（纯大模型）"],
        horizontal=True,
    )
    system = mode.split("（")[0]
    query = st.text_input("输入问题", value=EXAMPLES[0], key="query_input")
    cols = st.columns([1, 1, 3])
    if cols[0].button("提问", type="primary"):
        if not query.strip():
            st.warning("请输入问题")
            return
        with st.spinner("分析中..."):
            st.session_state["last_answer"] = get_agent().ask(query.strip(), system=system)
    cols[1].button("清空", on_click=lambda: st.session_state.pop("last_answer", None))

    st.write("示例：")
    for i, example in enumerate(EXAMPLES):
        if st.button(example, key=f"ex_{i}"):
            st.session_state["query_input"] = example
            with st.spinner("分析中..."):
                st.session_state["last_answer"] = get_agent().ask(example, system=system)

    answer = st.session_state.get("last_answer")
    if answer:
        st.divider()
        render_answer(answer)
    else:
        st.info("输入问题，或点击上面的示例。")


def page_operators() -> None:
    st.subheader("干员库（421 名可获取干员）")
    catalog = load_catalog()
    search = st.text_input("搜索干员", "").strip()
    rows = []
    for op in catalog.operators:
        if search and search not in op.name:
            continue
        supported = sum(1 for s in op.skills if s.calc_supported)
        rows.append(
            {
                "名称": op.name,
                "职业": op.class_name,
                "稀有度": op.rarity,
                "攻击": op.atk,
                "生命": op.hp,
                "防御": op.def_value,
                "法抗": f"{op.res_value:.0%}",
                "攻击间隔": op.attack_interval,
                "技能数": len(op.skills),
                "可计算": supported,
            }
        )
    st.caption(f"共 {len(catalog.operators)} 名干员；下表按名称过滤，共 {len(rows)} 条。")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=360)
    if not rows:
        return
    choice = st.selectbox("查看干员详情", [r["名称"] for r in rows])
    op = next(o for o in catalog.operators if o.name == choice)
    st.markdown(f"### {op.name}（{op.class_name}，{op.rarity}★）")
    st.write(
        f"精英 {op.phase} 级 {op.level}｜生命 {op.hp:g}｜攻击 {op.atk:g}｜"
        f"防御 {op.def_value:g}｜法抗 {op.res_value:.0%}｜攻击间隔 {op.attack_interval:g}s"
    )
    if op.trait:
        st.caption(f"特性：{op.trait}")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "技能": f"{s.skill_index}. {s.name}",
                    "类型": s.damage_type,
                    "攻击力加成": f"{s.atk_bonus:+.0%}",
                    "倍率": s.atk_multiplier,
                    "段数": s.hits,
                    "间隔修正": s.atk_time_flat,
                    "可计算": s.calc_supported,
                    "说明": s.unsupported_reason,
                    "计算口径": s.calc_note,
                }
                for s in op.skills
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def page_formulas() -> None:
    st.subheader("公式库（Ontology：Formula / Variable）")
    catalog = load_catalog()
    rows = [
        {
            "id": f.id,
            "名称": f.name,
            "类型": f.damage_type,
            "变量数": len(f.variables),
            "表达式": f.expression[:80] + ("..." if len(f.expression) > 80 else ""),
            "出处": f.citation.source_id,
        }
        for f in catalog.formulas
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    choice = st.selectbox("查看公式详情", [f.name for f in catalog.formulas])
    card = next(f for f in catalog.formulas if f.name == choice)
    st.markdown(f"### {card.name}（`{card.id}`）")
    st.code(card.expression, language="text")
    st.markdown("**变量**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "symbol": v.symbol,
                    "名称": v.name,
                    "单位": v.unit,
                    "必需": v.required,
                    "说明": v.description,
                }
                for v in card.variables
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.markdown("**适用条件**")
    for a in card.assumptions:
        st.markdown(f"- {a}")
    st.markdown("**出处**")
    st.markdown(f"[{card.citation.title} — {card.citation.section}]({card.citation.url})")
    st.caption(card.citation.quote)


def page_graph() -> None:
    st.subheader("知识图谱（Formula — Variable — Operator — Skill）")
    path = PROJECT_ROOT / "data" / "kb" / "graph.json"
    if not path.exists():
        st.warning("尚未构建 graph.json，请先运行 scripts/build_kb.py")
        return
    graph = json.loads(path.read_text(encoding="utf-8"))
    kinds = sorted({n["kind"] for n in graph["nodes"]})
    st.caption(
        f"当前图共 {len(graph['nodes'])} 个节点、{len(graph['edges'])} 条边。"
        "默认只显示公式-变量子图；勾选 operator 会加载 421 名干员，渲染较慢。"
    )
    selected = st.multiselect(
        "节点类型", kinds, default=["formula", "variable"]
    )
    from pyvis.network import Network

    net = Network(height="620px", width="100%", directed=True, bgcolor="#ffffff", font_color="#222")
    net.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=140)
    node_ids = {n["id"] for n in graph["nodes"] if n["kind"] in selected}
    colors = {
        "formula": "#e45756",
        "variable": "#4c78a8",
        "operator": "#54a24b",
        "skill": "#f58518",
    }
    for n in graph["nodes"]:
        if n["id"] in node_ids:
            net.add_node(
                n["id"],
                label=n["label"],
                title=f"{n['kind']}: {n['label']}",
                color=colors.get(n["kind"], "#999"),
            )
    for e in graph["edges"]:
        if e["source"] in node_ids and e["target"] in node_ids:
            net.add_edge(e["source"], e["target"], title=e["relation"], label=e["relation"])
    components.html(net.generate_html(), height=650, scrolling=True)


def page_eval() -> None:
    st.subheader("20 题评测面板")
    files = sorted((PROJECT_ROOT / "results").glob("eval_*.json"))
    if not files:
        st.warning("尚无评测结果，请先运行 scripts/run_eval.py")
        return
    latest: dict[str, Path] = {}
    for f in files:
        system = f.name.split("_")[1]
        if system not in latest or f.stat().st_mtime > latest[system].stat().st_mtime:
            latest[system] = f
    summaries = []
    records = []
    for system, path in latest.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(data["summary"])
        for r in data["records"]:
            r["system"] = system
            records.append(r)
    st.markdown("**最新一轮汇总**")
    df = pd.DataFrame(summaries)
    df = df.rename(
        columns={
            "system": "系统",
            "n": "题数",
            "numeric_accuracy": "数值正确率",
            "formula_accuracy": "公式命中率",
            "citation_accuracy": "引用正确率",
            "refusal_accuracy": "拒答正确率",
            "clarify_accuracy": "澄清正确率",
            "hallucination_rate": "幻觉率",
            "avg_latency_ms": "平均耗时(ms)",
        }
    )
    st.dataframe(df, width="stretch", hide_index=True)
    st.bar_chart(df.set_index("系统")[["数值正确率", "拒答正确率", "澄清正确率", "幻觉率"]])

    st.markdown("**逐题记录**")
    rdf = pd.DataFrame(records)
    show_cols = [
        c
        for c in ["system", "question_id", "predicted_value", "gold_value", "numeric_correct",
                  "formula_correct", "citation_correct", "refusal_correct", "clarify_correct",
                  "latency_ms"]
        if c in rdf.columns
    ]
    st.dataframe(rdf[show_cols], width="stretch", hide_index=True)
    with st.expander("查看某题完整回答"):
        rid = st.selectbox("题目", rdf["question_id"].tolist())
        row = rdf[rdf["question_id"] == rid].iloc[0]
        st.write(f"系统：{row['system']}")
        st.markdown(row["prediction"])


def page_audit() -> None:
    st.subheader("审计日志（每次问答的 decision record）")
    logs = sorted((PROJECT_ROOT / "results" / "logs").glob("*.jsonl"))
    if not logs:
        st.info("暂无日志，先在问答页提问。")
        return
    path = st.selectbox("日志文件", [p.name for p in logs])
    lines = (PROJECT_ROOT / "results" / "logs" / path).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    df = pd.DataFrame(rows)
    cols = [c for c in ["ts", "trace_id", "query", "intent", "formula_id", "refused",
                        "latency_ms", "retrieved", "citations"] if c in df.columns]
    st.dataframe(df[cols].iloc[::-1], width="stretch", hide_index=True)
    with st.expander("查看原始 JSON"):
        st.json(rows[-1] if rows else {})


def main() -> None:
    st.title("🎯 明日方舟数值问答与计算助手")
    st.caption(
        "Ontology-grounded Data Agent · 检索 → 规划 → 确定性计算 → 引用/审计 · "
        f"模型 {settings.chat_model}"
    )
    with st.sidebar:
        st.markdown("### 运行状态")
        st.write(f"API：{'已配置' if settings.has_key else '未配置'}")
        st.write(f"Key：`{settings.masked_key}`")
        st.write(f"检索：BM25 + `{settings.embed_model}` + `{settings.rerank_model}`")
        st.write("Semantica：ingest / parse / graph / provenance")
        st.divider()
        st.markdown("### 范围")
        catalog = load_catalog()
        n_supported = sum(1 for o in catalog.operators for s in o.skills if s.calc_supported)
        st.write(
            f"8 个公式族 · {len(catalog.operators)} 名干员 · "
            f"{n_supported} 个可计算技能 · 20 道评测题"
        )
        st.write("CN 服数据，固定 commit `63c7cec`")
        st.caption("全干员可查面板/技能；复杂技能会拒答计算并说明原因。")
    tabs = st.tabs(["问答", "干员库", "公式库", "知识图谱", "评测", "审计"])
    with tabs[0]:
        page_ask()
    with tabs[1]:
        page_operators()
    with tabs[2]:
        page_formulas()
    with tabs[3]:
        page_graph()
    with tabs[4]:
        page_eval()
    with tabs[5]:
        page_audit()


if __name__ == "__main__":
    main()
