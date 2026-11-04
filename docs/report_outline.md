# 期末报告提纲（中文）

> 说明：Semantica 选题 PPT 的最后一页沿用了 KDD Cup 版本的“60 题逐题得分/基线对比”表述，但本课题的评测是 20 题自建 benchmark。提交前建议向助教确认最终材料清单与评分口径；本提纲按“5 项必交 + 详细报告”组织。

## 0. 摘要（约 300 字）

- 问题：通用大模型记不住明日方舟的干员面板、技能倍率与保底规则，且无法追溯出处。
- 工作：构建一个 Ontology-grounded 的数值问答与计算助手；8 张公式卡、421 名干员实体（666 个可计算技能）、20 题评测。
- 结果：full 20/20；raw 4/12 数值题；rag 5/12 数值题（41.7%）、澄清 0/2、无结构化引用；full 平均 1.00 s。
- 结论：检索解决“事实”，确定性计算与治理解决“可信/可审计”。

## 1. 任务与领域理解

- 课题五要求：选一个“大模型没怎么见过”的窄领域，整理公式/变量/单位/适用范围/页码，回答时只引用知识库，缺失时拒答，并与通用大模型做 20 题对比。
- 为什么选明日方舟：公式分层多（攻击/倍率/防御/抗性/增伤/脆弱/减伤），wiki 与拆包数据公开，错误容易验证；PPT 也把“电子游戏”列为可选领域。
- 领域难点：
  - 5% 保底基于 `Atk_final`，不是技能倍率后的伤害；
  - 多段伤害逐段结算保底；
  - 物理固定减防 vs 法术比例减抗；
  - 攻击间隔的直接修正与 ASPD 顺序；
  - 脆弱同类取最高、减伤乘算。
- 输入输出：自然语言问题 → 公式/变量/干员实体 → 确定性计算 → 结论 + 步骤 + 出处。

## 2. Agent 系统架构

- 图：数据源 → Semantica ingest/parse → Ontology → 混合检索 → Planner → Calculator → Answer/Audit。
- 三个角色分工：
  - LLM：意图理解、实体/变量抽取的兜底、自然语言组织；**不做算术**。
  - Planner：规则优先，LLM 仅在低置信度时兜底；LLM 计划会被确定性修复。
  - Calculator：SymPy + Decimal，公式卡表达式与 Python 函数双实现交叉校验。
- Palantir 映射：Ontology / Operational truth / AIP / Action / Security / Evals；不实现 Apollo，用固定版本与脚本复现替代。
- 关键设计决策与理由（引用 `docs/architecture.md`）。

## 3. 知识库构建

- 数据来源：
  - 公式：Arknights Terra Wiki（wiki.gg）5 个机制页，快照 + 段落引用；
  - 数值：Kengxxiao/ArknightsGameData，固定 commit `63c7cec`，SHA256 记录在 `meta.json`。
- 公式卡 schema：id/name/aliases/expression/variables/assumptions/citation/examples。
- 干员/技能：`build_operators.py` 从 `character_table.json` / `skill_table.json` 的 M3 blackboard 生成，避免手抄错误。
- 全干员覆盖：421 名可获取干员、967 个技能进入实体库；666 个技能通过 `calc_supported` 分级，其余拒答计算并说明原因。
- Semantica 使用：
  - `FileIngestor` + `DocumentParser` 解析 wiki 快照；
  - `GraphBuilder` 重建 44 节点/47 边 ontology；
  - `ProvenanceManager` 为 8 张公式卡记录来源并导出 PROV。
- 产物：`data/kb/akcalc.db`、`corpus.jsonl`、`graph.json`、`semantica_graph.json`、`provenance.ttl`。

## 4. 关键模块实现

- 检索：jieba + BM25 + `ecnu-embedding-small` + `ecnu-rerank`，RRF 融合；无 key 降级。
- 规划：
  - 实体：rapidfuzz + 精确名优先；
  - 技能：技能名/“三技能”；
  - 变量：正则抽取 DEF/RES/ASPD/脆弱/减伤/攻击力；
  - 干员+技能自动填 `atk_final`、`atk_multiplier`、`hits`、`atk_time_flat`；
  - 缺变量 → clarify；超范围 → refuse。
- 计算：
  - 物理/法术/真实/治疗/攻速/DPS/最终伤害/最终攻击力 8 个函数；
  - 5% 保底、逐段结算、ROUND_HALF_UP、未取整值同时展示；
  - `evaluate_card` 用 SymPy 交叉校验公式卡。
- 答案与审计：模板化输出公式/步骤/出处；JSONL 记录 trace_id、plan、变量、引用、耗时、token。
- 演示：Streamlit 六页签（问答/干员库/公式库/知识图谱/评测/审计）。

## 5. 实验设置与结果

- 20 题：概念 3、计算 6、技能与增益 3、攻速 3、拒答 3、澄清 2。
- 三系统：full / rag / raw；同一模型 `ecnu-max`，temperature 0。
- 指标：数值、公式命中、引用、拒答、澄清、幻觉、耗时。
- 主表（放入报告）：

| 系统 | 数值 | 公式 | 引用 | 拒答 | 澄清 | 幻觉 | 耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.00 s |
| rag | 0.417 | N/A | N/A | 1.000 | 0.000 | 0.000 | 3.71 s |
| raw | 0.333 | N/A | N/A | 0.667 | 1.000 | 0.333 | 14.6 s |

- 分类别表：见 `results/by_category.csv`。
- 关键观察：
  - raw 的错误集中在面板/倍率记忆、5% 保底、攻速结算、跨领域；
  - rag 在 421 人语料下因实体歧义/上下文稀释降到 41.7%，澄清 0/2、无结构化引用；full 保持 100%。
  - full 的增量是确定性、可审计、治理与速度，而不是“数值更高”。

## 6. 案例与错误分析

- 成功案例：
  - 银灰 DEF=2100：106.95 → 107，展示 5% 保底；
  - 能天使 DEF=600：27×5=135，展示逐段保底；
  - 能天使攻速：0.89 → 0.742，展示直接修正 + ASPD 顺序；
  - 银灰 +30% 脆弱：1639 → 2130.7。
- 失败案例（raw/rag）：
  - raw 把火山 M3 记成 +85%，835 vs 1038；
  - raw 忽略 5% 保底，0 vs 74；
  - raw 把攻击间隔当 ×0.25，0.25 vs 0.89；
  - rag 先给 DEF=0 的 2139，同时承认不知道 DEF；
  - rag DPS 自行假设防御，3337.08 vs “需要澄清”。
- 评测器误差与修正（M3 标记、boxed、粗体、拒答标记）单列一小节，体现严谨性。

## 7. 系统优化与对比

- 优化 1：规则 planner + LLM 兜底 → 降低 LLM 公式漂移；对比“纯 LLM 规划”的错误。
- 优化 2：公式卡双实现 + SymPy 校验 → 防止 YAML 与代码不一致。
- 优化 3：评测 corpus 移除示例答案 → 消除 RAG 数据泄漏；早期 RAG 虚高，修复后重跑。
- 优化 4：结构化 citation + trace → 可审计；raw/rag 无此能力。
- 与教师基线：本课题没有 KDD 的 75.6387 基线；如老师给出统一评分口径，补充对应对比。

## 8. 复现与参考说明

- 环境：Python 3.11、uv、依赖版本、`.env` 模板。
- 命令：fetch → build_operators → build_kb → build_semantica → smoke → run_eval。
- 固定版本：游戏数据 commit、wiki 抓取日期、模型名。
- 参考项目：Semantica（ingest/parse/graph/provenance）、Kengxxiao/ArknightsGameData、Arknights Terra Wiki。
- AI 工具：Claude Code 辅助代码/文档；公式与数值人工校订；评测由脚本实际运行。

## 9. 个人贡献与分工（按实际填写）

- 领域选择与公式校订；
- 数据管线与知识库；
- Agent/计算器/评测；
- UI 与演示；
- 报告撰写与结果复核。
- 若使用 AI 工具，明确列出用于哪些部分、如何验证。

## 10. 图表清单

1. 系统架构图（Mermaid 渲染）。
2. Ontology 子图（公式-变量-干员-技能）。
3. Streamlit 问答页截图（含公式/步骤/出处）。
4. 三系统指标柱状图。
5. 典型错误对比表（raw vs full）。

## 11. 提交前检查

- [ ] 代码可运行、`.env` 未提交、数据可重建。
- [ ] 20 题结果文件与报告数字一致。
- [ ] 引用 URL/段落可点开核对。
- [ ] 报告中明确说明 AI 工具与参考代码的使用。
- [ ] 向助教确认提交清单（Semantica PPT 最后一页的“60 题”表述不适用于本课题）。
