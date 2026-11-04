# 数据来源与复现

## 1. 来源总表

| source_id | 内容 | URL | 本地快照 | 抓取日期 |
|---|---|---|---|---|
| `wiki_gg_damage_physical` | 物理伤害公式、5% 保底、最终伤害乘区 | https://arknights.wiki.gg/wiki/Damage/Physical | `data/raw/wiki/wiki_gg_damage_Physical.html` | 2026-11-04 |
| `wiki_gg_damage_arts` | 法术伤害公式、法抗 | https://arknights.wiki.gg/wiki/Damage/Arts | `data/raw/wiki/wiki_gg_damage_Arts.html` | 2026-11-04 |
| `wiki_gg_damage_true` | 真实伤害定义 | https://arknights.wiki.gg/wiki/Damage/True | `data/raw/wiki/wiki_gg_damage_True.html` | 2026-11-04 |
| `wiki_gg_damage` | 伤害类型总览 / 治疗说明 | https://arknights.wiki.gg/wiki/Damage | `data/raw/wiki/wiki_gg_damage.html` | 2026-11-04 |
| `wiki_gg_attribute_attack_interval` | 攻击间隔 / ASPD 公式 | https://arknights.wiki.gg/wiki/Attribute/Attack_interval | `data/raw/wiki/wiki_gg_Attribute_Attack_interval.html` | 2026-11-04 |
| `wiki_gg_damage_reduction` | 减伤机制 | https://arknights.wiki.gg/wiki/Damage_Reduction | `data/raw/wiki/wiki_gg_Damage_Reduction.html` | 2026-11-04 |
| `gamedata_63c7cec73c38` | 干员/技能/敌人手册/攻击范围 | https://github.com/Kengxxiao/ArknightsGameData/tree/63c7cec73c3858d0ae913f85cc89af3cef4d744f/zh_CN/gamedata/excel | `data/raw/gamedata/63c7cec73c38/` | 2026-11-04 |

## 2. 游戏数据固定版本

- 仓库：`Kengxxiao/ArknightsGameData`（社区从官方 CN 客户端拆包）
- commit：`63c7cec73c3858d0ae913f85cc89af3cef4d744f`
- commit 时间：2026-11-01T02:17:15Z
- 服务器：`zh_CN`

| 文件 | 大小（bytes） | SHA256（前 16 位） |
|---|---:|---|
| `character_table.json` | 15,468,242 | `6a61f54802ee194d` |
| `skill_table.json` | 11,766,431 | `492900f6b9572332` |
| `enemy_handbook_table.json` | 1,895,825 | `891421a9208359f1` |
| `range_table.json` | 53,729 | `221c7bbc589c3fb6` |

完整 SHA256、下载 URL 与抓取时间写入 `data/raw/gamedata/63c7cec73c38/meta.json`。

## 3. 为什么用 wiki.gg，而不是 PRTS / mrfz.wiki

| 站点 | 可达性 | 结论 |
|---|---|---|
| `prts.wiki` | 首页可访问，但连续 API/页面请求会触发 WAF 403 | 作为人工核对来源，不作为自动抓取主源 |
| `mrfz.wiki` | 当前网络不可达（连接超时） | 未使用 |
| `arknights.wiki.gg` | 可达，机制页结构清晰，公式段落完整 | **主源** |
| `wiki.biligame.com/arknights` | 可达，中文 wiki | 备用/人工核对来源 |

抓取时遵循限速与缓存原则，不整体再分发 wiki 内容；报告与 UI 均保留原 URL 与段落名。

## 4. 数据管线

```bash
# 0. 下载 wiki 机制页快照
python scripts/fetch_wiki.py

# 1. 下载并固定游戏数据（jsDelivr gcore 镜像）
python scripts/fetch_gamedata.py

# 2. 生成干员/技能 YAML（M3 数值来自 skill_table.json blackboard）
python scripts/build_operators.py   # 生成 operators_all.yaml(421 名) 与 operators.yaml(6 人基准)

# 3. 构建 SQLite / corpus / graph
python scripts/build_kb.py

# 4. Semantica 解析样例、重建图、导出 PROV
python scripts/build_semantica_artifacts.py
```

`scripts/fetch_gamedata.py` 会：

- 查询 GitHub API 获取 `master` 当前 commit；
- 以 `@<commit>` 固定版本从 `gcore.jsdelivr.net` 下载；
- 计算 SHA256 并写入 `meta.json`；
- 失败时依次尝试 `testingcf.jsdelivr.net`、`cdn.jsdelivr.net`。

## 5. 人工校订

- `data/curation/formulas.yaml`：8 张公式卡，表达式、变量、适用条件、出处与示例均为人工整理；
- `data/curation/operators_all.yaml`：421 名可获取干员、967 个技能，按黑板参数自动标记 `calc_supported`（667 个可计算）；
- `data/curation/operators.yaml`：20 题基准使用的 6 名干员子集；技能等级取 `levels[9]`（M3/最高等级）；
- `atk_final` 卡采用“ATK% 加算”的简化式，并在字段与文档中标注适用范围；复杂跨天赋叠加不在当前范围。

## 6. 许可与署名

- Arknights 游戏内容与素材版权归 Hypergryph / Studio Montagne 所有；本项目仅用于课程学习与研究。
- Arknights Terra Wiki（wiki.gg）内容按其许可使用，仅在本地保留快照用于课程复现，不整体再分发。
- `Kengxxiao/ArknightsGameData` 为社区拆包仓库；本项目引用其固定 commit，不主张对游戏数据的任何权利。
- 报告与 UI 中均展示来源标题、段落与 URL；`data/sources/sources.yaml` 为机器可读的来源注册表。

## 7. 复现注意事项

- 游戏版本更新后，`master` 会前进；复现旧结果请使用上面的 commit，而不是 `master`。
- 若 jsDelivr 镜像不可用，可改用 GitHub raw 或本地代理，但必须保留 `meta.json` 中的 SHA256。
- wiki 页面可能改版；本地快照是评测时的“事实版本”，报告应注明抓取日期。
- 不要把 `.env`、API key 或含个人信息的日志提交到仓库。
