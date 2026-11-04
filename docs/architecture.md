# 系统架构与设计说明

## 1. 目标与边界

目标：给定一道中文数值问题，系统能够

1. 识别问题涉及的公式、干员与技能；
2. 从知识库检索公式定义、变量说明与干员数值；
3. 绑定变量并做**确定性计算**；
4. 输出公式、代入步骤、结果、适用条件与出处；
5. 在信息不足时追问，在超出知识库时拒答；
6. 把每次决策写入审计日志，支持 20 题批量评测。

边界：实体库覆盖 CN 服 421 名可获取干员、967 个技能；其中 666 个技能通过可计算性分级，其余只做展示、计算时拒答。公式卡 8 张，技能等级取 M3/最高等级；不处理潜能/信赖/模组/敌人数据库/元素伤害；AoE 技能按单目标计算，不乘目标数。

## 2. 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│ 数据源层                                                     │
│  wiki 机制页快照(HTML)   官方拆包 JSON(固定 commit)           │
│  data/raw/wiki           data/raw/gamedata/63c7cec...        │
└───────────────────────┬──────────────────────────────────────┘
                        │ Semantica FileIngestor + DocumentParser
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ Ontology 层（Palantir Ontology 的轻量实现）                  │
│  Formula ──requires──▶ Variable                              │
│  Operator ──has_skill──▶ Skill ──uses_formula──▶ Formula      │
│  每个对象带 citation(source_id/title/url/section/quote)       │
│  存储：SQLite(data/kb/akcalc.db) + graph.json + Semantica 图  │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ 检索层                                                       │
│  jieba + BM25  ─┐                                            │
│  ecnu-embedding ─┼─▶ RRF 融合 ─▶ ecnu-rerank ─▶ top-k 文档   │
│  （无 key 时自动降级为纯 BM25）                               │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ 规划层（LLM 只做理解，不做算术）                              │
│  规则 planner：实体链接 + 数值抽取 + 公式选择                 │
│  LLM 兜底：输出 Intent/FormulaId/EntityLinks/Variables JSON   │
│  修复：变量从 catalog 重新推导，LLM 不能覆盖确定性数值        │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ 动作层（Deterministic Action）                                │
│  SymPy 符号代入 + Decimal 求值 + ROUND_HALF_UP                │
│  5% 保底、逐段结算、DEF/RES 下限、ASPD/直接间隔顺序           │
│  公式卡表达式与 Python 函数交叉校验（不一致则 warning）        │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ 治理与可观测层                                                │
│  缺变量 → clarify；超范围 → refuse；答案必须带 citation       │
│  results/logs/*.jsonl 记录 query/plan/calc/citations/tokens   │
│  20 题评测：数值/公式/引用/拒答/澄清/幻觉                     │
└──────────────────────────────────────────────────────────────┘
```

## 3. Ontology 对象

| 对象 | 关键字段 | 说明 |
|---|---|---|
| Formula | id, name, aliases, expression, variables, assumptions, citation | 一张公式卡对应一个可执行表达式 |
| Variable | symbol, name, unit, required, min/max | 变量与单位/取值范围 |
| Operator | id, name, class, rarity, atk, def, res, hp, attack_interval | E2 满级面板 |
| Skill | id, operator_id, name, atk_bonus, atk_multiplier, heal_multiplier, atk_time_flat, aspd_flat, damage_type, hits, calc_supported | M3/最高等级数值 + 可计算性标记 |
| Calculation | formula_id, inputs, steps, raw_value, display_value, citations | 一次可审计的计算记录 |

关系：`Formula --requires--> Variable`、`Operator --has_skill--> Skill`、`Skill --uses_formula--> Formula`、`Skill --uses_formula--> atk_final`（有攻击力加成时）。

### 全干员覆盖分级

- **实体层**：421 名可获取干员、967 个技能全部进入 SQLite、corpus 与 graph；可回答面板/技能/职业/稀有度查询。
- **计算层**：666 个技能通过黑板参数分级（`calc_supported`），可做单次/多段伤害、治疗、攻击间隔、DPS、攻击力计算。
- **拒答层**：召唤、浮游、弹药、AoE、元素、持续伤害等未建模技能会明确拒答，并给出原因；不会用近似公式冒充精确结果。
- 当前图谱规模：1420 个节点、1859 条边；corpus 1396 篇文档。

## 4. 运行时序

1. **检索**：query 同时走 BM25 与向量召回，RRF 融合后 rerank，返回公式/干员/技能文档。
2. **规划**：
   - 规则 planner 先用 `rapidfuzz` 匹配干员名，识别“三技能/技能名”，再按关键词与技能伤害类型选公式；
   - 用正则抽取 DEF/RES/ASPD/脆弱/减伤/攻击力等数值；
   - 干员+技能可自动填充 `atk_final`、`atk_multiplier`、`hits`、`atk_time_flat`；
   - 规则置信度不足时才调用 LLM 输出 JSON 计划；
   - LLM 计划会经过 `_repair_plan`：从 catalog 重新推导变量，缺必需变量则改为 clarify。
3. **计算**：`evaluate_card` 调用对应 Python 计算函数，并用 SymPy 重新求值公式卡表达式做交叉校验。
4. **后处理**：若问题含脆弱/减伤，把已结算伤害再套 `final_damage` 卡。
5. **生成答案**：模板输出结论、公式、逐步代入、注意事项、出处；不调用 LLM 做二次算术。
6. **审计**：写 JSONL，包含 trace_id、plan、变量、引用、耗时、token。

## 5. Palantir 映射

| Palantir 概念 | 本项目对应 |
|---|---|
| Ontology（objects/links/actions/security） | Formula/Variable/Operator/Skill + 计算动作 + 范围校验 |
| Operational truth | 只回答知识库内、可计算、可引用的问题 |
| AIP（grounded LLM） | LLM 只做意图理解与 JSON 规划，上下文来自检索 |
| Actions / write-back | 确定性计算并返回可复核的步骤 |
| Security / governance | 缺变量追问、超范围拒答、引用必填、审计日志 |
| Evals / observability | 20 题三系统对比 + per-request trace |
| Apollo（部署） | 不实现；用固定 commit、脚本化构建与复现文档替代 |

## 6. 关键设计决策

1. **LLM 不做算术**：所有数值由 SymPy/Decimal 计算，避免模型心算误差与公式编造。
2. **规则优先、LLM 兜底**：规则 planner 对课程题库稳定；LLM 只处理未覆盖表达。LLM 计划不能替换规则已高置信度选出的公式。
3. **公式卡双实现**：YAML 表达式用于展示/交叉校验，Python 函数用于计算；两者不一致会产生 warning。
4. **引用即数据**：citation 进入 Formula/Operator 对象，答案与 UI 都直接展示；不依赖模型“记得引用”。
5. **拒答与澄清分开**：超范围是 refuse，缺变量是 clarify；两者在评测中分别计分。
6. **可降级**：无 API key 时检索降级为 BM25；Semantica 不可用时核心仍可运行。

## 7. 失败模式与应对

| 失败模式 | 现象 | 应对 |
|---|---|---|
| 实体歧义 | “银灰”匹配到其他文本 | rapidfuzz 阈值 + 干员名精确优先 |
| 变量缺失 | 只问“伤害是多少” | clarify，列出缺少的变量中文名 |
| 公式混淆 | 物伤/法伤/真伤选错 | 关键词 + 技能 damage_type + LLM 兜底 |
| 取整争议 | 106.95 显示 106 或 107 | 文档化 ROUND_HALF_UP；同时给出未取整值 |
| 复杂干员 | 玛恩纳/澄闪机制特殊 | 明确拒答计算，避免给出错误数值 |
| 检索泄漏 | 公式卡示例含 gold 数值 | 评测 corpus 移除 examples；缓存加文本指纹 |
| 版本漂移 | 游戏更新导致数值变化 | 固定 commit + meta.json sha256 + 重跑脚本 |

## 8. 扩展点

- 增加干员：在 `SELECTION` 中加映射，重跑 `build_operators.py`。
- 增加公式：在 `formulas.yaml` 加卡，在 `calculator.py` 注册函数，补单测。
- 增加数据源：在 `sources.yaml` 注册，扩展 `fetch_wiki.py`（待实现）与 Semantica parse。
- 接入其他模型：改 `.env` 的 base_url/model；并发信号量按模型名配置。
- 更严格评测：扩大题量、多次采样、加入独立人工评分。
