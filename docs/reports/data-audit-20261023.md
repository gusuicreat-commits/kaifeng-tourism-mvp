# 开封主城区旅游数据工程——现有项目审计（阶段一）

日期：2026-10-23。范围：kaifeng-tourism-mvp 仓库现状、`开封旅游数据采集_20261020` 独立采集包、`data/collection-20261023*` 扩展证据包。本审计只读现状，不改动任何数据。

## 结论

- 已有可复用的核心能力：`city-facts/1` 版本化事实契约（`backend/app/city/facts.py`，288 行，覆盖 source/place/entrance/open_rule/program/show_session/food/vendor/supply）+ 维护 CLI `scripts/import_city_facts.py`（默认内存校验，`--apply` 才原子写入，无游客写入口）。
- 运行库 `data/tourism.sqlite3`：`city_facts` 0 条、`scenic` 5 条（演示种子）、`schedule_facts` 0 条、`observations` 504 条（全部为模拟客流）。即：正式城市事实尚未入库，现有内容是演示数据。
- 已有一轮真实来源扩展采集（`data/collection-20261023-expanded`）：112 条 city-facts/1 候选记录（48 place、55 source、9 food），覆盖 24 个 A 级景区候选、7 家餐馆分店、3 个夜市。该包自评为"非可执行证据包"：无一条可执行路线、无目标日演出场次、无门店供应证明。
- 缺口：缺少通用的采集任务/运行日志/失败记录表、来源登记表不在数据库内、无去重与空间检查程序、无数据质量统计报表、无增量更新机制、无主城区范围配置文件。

## 现有数据表（data/tourism.sqlite3）

| 表 | 行数 | 性质 |
| --- | --- | --- |
| city_facts / city_fact_revisions | 0 | 正式城市事实入口（空） |
| scenic | 5 | 演示种子景区 |
| schedule_facts | 0 | 旧版节目/开放导入表（空） |
| observations | 504 | 模拟客流 |
| networks | 2 | 本地示意路网（非真实道路） |
| city_trip_states / city_baskets / city_engine_* 等 | - | 城市行程与引擎状态，与本次数据工程不冲突 |

## 已有采集能力

- `data/collection-20261023-expanded/tools/build_expanded.py`：从 raw/ 搜索与直读快照构建结构化 city-facts/1 候选包，含来源登记、冲突清单、review HTML。属于一次性脚本，未参数化、无任务日志、无断点续跑。
- `开封旅游数据采集_20261020/scripts`：上一轮 OSM/官网采集脚本与 30+ 张 CSV/JSON 表，含 SHA256 校验清单。
- `scripts/import_city_facts.py`：唯一受控入库通道，符合"证据先行、整批原子导入、默认 dry-run"要求，本次应复用而不是新建并行导入路径。

## 字段与规划器匹配

- city-facts/1 已覆盖规划器需要的地点、入口、营业规则、节目、场次、菜品、商家、供应关系，且强制记录来源、核验状态（unverified/verified）、业务状态、权限状态（rights_status）。
- EvidenceKind 五级（verified/estimate/reported/demo/unknown）与"缺失值不允许 AI 猜测"的要求一致，应继续沿用，不新增并行状态体系。

## 主要数据缺口（优先级从高到低）

1. 主城区范围配置：无可配置的采集区域（城区多边形/建成区判定），无法做空间过滤。
2. 地点覆盖：24 个 A 级候选里多数只有名称，缺地址、坐标、开放规则；博物馆、街区、公园、夜游、文化场所类尚未系统发现。
3. 目标日演出场次：清明上河园 154 条、开封府 24 条均为目录/起始时刻，无结束时刻与适用日。
4. 餐饮供应关系：9 条 food 记录无 vendor 关联与供应证据。
5. 交通衔接：公交站、枢纽、入口—站点衔接只有候选，无 2026 目标日依据。
6. 便民设施：基本为零。
7. 工程机制：任务日志、失败记录、断点续跑、去重、质量报告、更新调度全部缺失。

## 复用决策

- 入库只走 `city-facts/1` + `import_city_facts.py`，不新建重复事实表。
- 新增的数据工程基础设施（来源登记、采集运行、失败记录、核验任务）建独立的数据工程表，不污染城市事实契约。
- 采集代码集中到新目录 `collector/`（包）+ `data/collection-runs/`（运行产物），与既有 `data/collection-20261023*` 证据包并列但机制化。
- 三个 Skill 落盘到 `~/.codex/skills/`，脚本与仓库内 collector 包共享，避免 Skill 与代码两套逻辑漂移。

## 风险与边界

- 官方站点多有 WAF（本轮 403 记录），需要限流、重试、失败登记，不绕过访问控制。
- 高德等地图 API 无 Key，本轮交通只做来源登记与接口预留，不伪造估时。
- 仓库尚未有初始 git commit（所有文件未跟踪），本次改动同样保持未提交状态，由用户决定提交时机。
