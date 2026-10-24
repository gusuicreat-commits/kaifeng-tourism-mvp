# 开封主城区旅游数据采集流水线——交付说明

版本 v1.0，2026-10-24。本文汇总"小汴梁"主城区旅游数据自动化采集系统的建设结果，是验收闭环后的交付记录。

## 验收结论

**通过**（k3 验收线程独立复跑确认，非凭报告）。

阶段三"第一批真实数据闭环可用"7 项门槛全部满足：类别覆盖 ≥3（实际 6 类）、城区覆盖 ≥2（实际 5 城区）、来源可追踪 100%、核验状态如实、dry-run 零 schema 错误、机制可证（任务→运行→失败登记链路完整、verify_task 幂等）、红线归零（无虚构、无 AI 补全、无绕过导入通道、无绕过 WAF）。

## 交付清单

### 文档
- `docs/reports/data-audit-20261023.md` — 阶段一项目审计
- `docs/DATA_ENGINEERING_PLAN.md` — 数据工程总体规划（范围/类别/台账/Schema 映射/验收标准/更新机制）
- `docs/DATA_QUALITY_REPORT_SPEC.md` — 质量报告 8 项指标口径

### 采集程序（collector/ 包）
- `collector/config.py` + `collector/config/{collection_scope,categories,fetch_policy}.yaml` — 主城区范围、类别、限流/重试/robots 配置
- `collector/ledger.py` — SQLite 台账 `data/collection.db`（source_registry/collect_task/collect_run/collect_failure/verify_task），verify_task 按 (fact_ref, check_item) 幂等
- `collector/fetch.py` — httpx 抓取：限流、超时、指数退避重试、按 User-agent 段解析 robots.txt、401/403/429 记失败不绕过、原始响应 SHA-256 存档
- `collector/discover.py` — 来源发现（城区×类别×关键词矩阵）
- `collector/extract.py` — 结构化提取（纯代码规则为主，LLM 接口预留不默认调用）
- `collector/validate.py` — 名称归一、去重、粗空间过滤、冲突保留、review 任务生成、city-facts/1 候选包输出
- `collector/quality.py` — 8 项指标计算
- `collector/report.py` — markdown + JSON 质量报告
- `collector/run_collection.py` — 批量采集编排

### Skill（~/.codex/skills/）
- `kaifeng-source-discovery` — 发现并登记候选来源
- `kaifeng-data-collection` — 批量采集执行
- `kaifeng-data-validation` — 整理、核验与质量报告

三个 Skill 只封装流程，核心逻辑全部在仓库 `collector/` 包内，不复制实现。

### 真实采集结果

| run | 记录数 | 类别 | 来源 | 失败 | 报告 |
| --- | --- | --- | --- | --- | --- |
| `run_1d70ae0fdd19`（2026-10-23） | 48 | museum 22 / restaurant 12 / food 7 / night_market 5 / street_block 1 / entrance 1 | 25 | 0 | `data/collection-runs/run_1d70ae0fdd19/reports/quality-report-2026-10-23.md` |
| `run_10712c5a07e1`（2026-10-24） | 4 | museum 1 / street_block 1 / night_market 1 / entrance 1 | 26 累计 | 3（真实：404/401/robots） | `data/collection-runs/run_10712c5a07e1/reports/quality-report-2026-10-24.md` |

### 阶段四续：三件补齐任务（2026-10-24，k3 已验收通过）

针对 k3 上一阶段列出的第一阻塞项，执行线程（step-5）完成三个独立 run：

| run | 内容 | 结果 |
| --- | --- | --- |
| `coord_backfill_20261024` | 用本地 OSM（WGS84/ODbL）与团队工作簿 GCJ-02 坐标回填前两轮无坐标 place | 45 条中匹配 3 条（GCJ-02 餐馆），其余如实 unmatched + 核验任务；未调用需 Key 地图 API、未做坐标系转换猜测 |
| `open_rules_2026-10-24` | `12_open_rules` 19 条周期规则按目标日 2026-10-24 实例化 | 16 条 open_rule + 15 place + 27 source；3 缺口（expired 规则、冬季未生效、跨日夜市待人工拆分） |
| `scenic_align_20261024` | 团队工作簿 12 景区以 `place_type=scenic` 落候选，跨命名空间去重核验 | 12 景区 + 13 source，全部 unverified/internal_only |

- 三 run bundle 均 dry-run 零 schema 错误（coord 69 / open_rules 58 / scenic 25）。
- 新增模块：`collector/geocode_backfill.py`、`collector/open_rules_instantiator.py`、`collector/scenic_align.py`（及 `coordinate_run.py`、`scenic_run.py`）。
- OSM 坐标源记录 ODbL-1.0 许可；工作簿坐标源记内部移交。

### 团队工作簿接入
- `scripts/import_team_workbook.py` — 工作簿 v1.1 → city-facts/1 候选包转换器
- `data/team-handoff/` — 原始 xlsx + `city-facts-teamhandoff-20261023.json`（469 条候选：source 177 / place 68 / vendor 32 / food 26 / supply 48 / entrance 20 / program 98）+ README

## 验证结果

- `collector/tests`：249 passed（阶段四续新增坐标回填/开放规则/景区对齐测试后从 139 增至 249）
- `backend/tests`：679 passed（另一会话的 redteam 测试已自行修复）
- bundle dry-run（两轮）：零 schema 错误，`ready=0` 符合候选预期
- 三件补齐任务 bundle dry-run：零 schema 错误（coord 69 / open_rules 58 / scenic 25）
- `data/tourism.sqlite3` 的 `city_facts`：0 行，未 `--apply`（候选不入正式库）

## 关键纪律（贯穿全流程）

- 所有采集记录均为 `unverified` + `internal_only` 候选，无任何一条被升级为 verified。
- 缺失即缺失：坐标、营业时间、门票、场次、入口方向等未知字段一律保留 null/待核验，禁止 AI 猜测补全。
- 入口方向未确认时不生成 `kind=entrance` 关系（`direction` 无 `unknown` 枚举），只落 place 层 parent 关联并生成人工核验任务。
- 不绕过验证码/登录/robots/WAF；401/403/429 记入 `collect_failure` 表。
- 入库唯一通道 `scripts/import_city_facts.py`，默认 dry-run，`--apply` 需显式选择数据库。

## 下一阶段优先级（k3 建议）

> 本节 1–3 项已在 2026-10-24 阶段四续完成并验收通过（见上表）。剩余未解决项：

1. 景区中心点坐标仍全缺：OSM 表只有 11 个点坐标且均为门/售票处/内部点，无景区级中心点，需官方坐标或实地/高德确认。
2. 场次（`show_session`）与供应证明需现场或运营商确认，不建议继续用平台推荐菜扩张。
3. 示范区管委会名录页已 404（真实失败登记发现），需找替代来源。
4. 阶段六接入规划引擎：将已核验候选通过 `scripts/import_city_facts.py --apply` 入库（破坏性动作，需明确批准）。

## 复现命令

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp
# 采集测试
.venv/bin/python -m pytest collector/tests -q
# 后端回归
.venv/bin/python -m pytest backend/tests -q
# 第一轮 bundle dry-run
.venv/bin/python scripts/import_city_facts.py data/collection-runs/run_1d70ae0fdd19/bundle/city-facts-candidate.json --database data/tourism.sqlite3
# 团队工作簿转换
.venv/bin/python scripts/import_team_workbook.py "data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx" --out data/team-handoff/city-facts-teamhandoff-20261023.json
```
