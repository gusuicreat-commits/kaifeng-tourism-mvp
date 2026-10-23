# 开封主城区旅游数据工程总体规划

> 2026-10-23实施增量：主目录现已有 `collector/`、范围/类别/抓取策略配置、`data/collection.db` 和批次产物。抽取、bundle校验/生成集中在 `extract.py` / `validate.py`，质量报告由 `quality.py` / `report.py` 生成；下文 `bundle.py` 属原规划命名，不代表实际文件。阶段二/三仍应按门槛逐条验收，代码和产物存在不等于整阶段通过；本轮不改在进行中的采集实现或既有批次报告。

版本 v1.0，2026-10-23。依据 [data-audit-20261023.md](reports/data-audit-20261023.md)、[DATA_SOURCES.md](DATA_SOURCES.md)、[DATA_REQUIREMENTS.md](DATA_REQUIREMENTS.md)、[DEVELOPMENT_DIRECTION.md](DEVELOPMENT_DIRECTION.md)、[PRD.md](PRD.md) 与 `backend/app/city/facts.py`（city-facts/1 契约）制定。本文是数据工程规划，不表示任何采集已经完成。

## 定位与硬约束

- 本规划只覆盖"如何把主城区旅游数据持续、可审计地采集并导入运行库"。不重复建设 OR-Tools 规划算法，不改前端，不引入新服务。
- 入库唯一通道是 `scripts/import_city_facts.py`（city-facts/1）。任何采集产物必须整理成 city-facts/1 导入包，经 dry-run 校验后由人工决定 `--apply`。不设计、不允许并行导入路径。
- 运行库是 `data/tourism.sqlite3`（SQLite）。数据工程台账用独立的 SQLite 文件 `data/collection.db`，与运行库物理隔离。PostgreSQL 是远期选项，本期不引入。
- 所有网页内容视为不可信输入。网页正文不得进入任何提示词上下文当作指令；采集器只做字段抽取，抽取规则由代码决定，不由网页内容决定。缺失即缺失，不允许 AI 猜测补全。
- 演示数据与真实候选分开；`is_demo=false` 且 `validation_status` 如实标记。`unknown` 不得变成路线、成本、商家、拥挤或导航事实。

## 1. 主城区采集范围定义策略

### 1.1 范围声明

采集范围限定为开封主城区连续建成区，覆盖以下市辖区的主城部分：

| 城区 | 定位 |
| --- | --- |
| 龙亭区 | 核心景区密集区（清明上河园、万岁山、龙亭、翰园、铁塔、天波杨府等） |
| 鼓楼区 | 老城商业与夜市核心区（鼓楼夜市、大相国寺、开封府、包公祠等） |
| 顺河回族区 | 老河大夜市、汴梁小宋城方向 |
| 禹王台区 | 禹王台公园及城南片区 |
| 城乡一体化示范区 | 西部新城建成区（按实际连续建成区判定） |

### 1.2 边界处理规则

- 明确排除：杞县、通许县、尉氏县、兰考县、祥符区的非主城区部分（祥符区原为开封县，其城关不属于主城连续建成区）。
- 边界不确定的地点（如示范区与龙亭区交界、城市边缘公园）打 `boundary_pending` 标记，列入人工核验任务，**不编造精确行政边界或多边形坐标**。
- 范围配置落盘为 `collector/config/collection_scope.yaml`：城区清单、排除清单、边界待定标记。空间过滤用粗粒度包围盒（人工核定的近似矩形，注明"近似、待核验"），不伪造精细多边形。
- 每条 place 记录 `district` 字段（龙亭/鼓楼/顺河/禹王台/示范区/待核验），城区归属不确定时标 `待核验`，不猜测。

## 2. 旅游资源类别配置设计

类别配置落盘为 `collector/config/categories.yaml`，与 city-facts/1 的映射如下：

| 采集类别 | city-facts/1 落点 | 关键字段要求 |
| --- | --- | --- |
| 景区（A 级及重点） | place_type=scenic | 名称、城区、地址/坐标候选、来源 |
| 博物馆/文化场所 | place_type=poi | 同上；开放规则单独采集 |
| 历史街区 | place_type=poi 或 night_visit | 街区与单店分开建档 |
| 公园 | place_type=poi | 同上 |
| 美食（菜品） | kind=food | 菜名、来源 |
| 餐饮商家 | place_type=restaurant + kind=vendor | 门店名、地址、营业候选 |
| 供应关系 | kind=supply | 必须带 evidence_note，平台推荐菜只算候选不算证明 |
| 夜市/夜游 | place_type=night_visit | 开市依据；临时休市不外推 |
| 交通设施 | place_type=transport | 车站、公交枢纽候选；无 Key 不伪造估时 |
| 便民设施 | place_type=rest | 休息点、卫生间等，逐步补 |
| 入口 | kind=entrance + place_type=entrance | 不拿地图中心点/停车场冒充入口 |
| 开放规则 | kind=open_rule | business_date 明确；采集日≠游玩日 |
| 节目/场次 | kind=program + kind=show_session | 场次必须有日期；无结束时刻保留缺口 |

## 3. 数据工程台账表设计（data/collection.db）

独立 SQLite 文件，由 `collector/` 包内 `ledger.py` 初始化与读写。这些表是**数据工程台账**，不是游客事实库；不与 `city_facts` 重复存储事实内容，只记工程元数据。

### 3.1 source_registry（来源登记表）

| 字段 | 说明 |
| --- | --- |
| source_key | 台账内稳定 ID |
| name / url / publisher / source_type | 与 city-facts/1 Source 对齐 |
| access_kind | official_site / platform / search_snapshot / field / map_service |
| access_status | reachable / waf_blocked / stale / retired |
| first_seen_at / last_checked_at | 时间戳 |
| content_sha256 / raw_path | 原始证据哈希与落盘路径 |
| notes | 访问限制、复用边界 |

### 3.2 collect_task（采集任务表）

| 字段 | 说明 |
| --- | --- |
| task_id | 唯一 ID |
| category / scope_district | 类别与城区 |
| objective | 任务目标（人读） |
| status | pending / running / done / failed / cancelled |
| priority / created_by / created_at | 调度元数据 |

### 3.3 collect_run（运行记录表）

| 字段 | 说明 |
| --- | --- |
| run_id / task_id | 关联任务 |
| started_at / finished_at / status | success / partial / failed |
| items_fetched / items_new / items_changed / items_failed | 统计口径见质量报告规范 |
| output_dir / report_path | 产物路径（`data/collection-runs/<run_id>/`） |
| error_summary | 顶层错误摘要 |

### 3.4 collect_failure（失败记录表）

| 字段 | 说明 |
| --- | --- |
| failure_id / run_id / source_key | 关联运行与来源 |
| failure_kind | http_error / waf_block / parse_error / schema_error / timeout |
| detail / occurred_at / retry_of | 支持断点续跑时重试关联 |

### 3.5 verify_task（人工核验任务表）

| 字段 | 说明 |
| --- | --- |
| verify_id / fact_ref | 指向 city-facts/1 的 (kind, fact_id) |
| check_item | 待核验字段（坐标/营业时间/场次结束/供应证明等） |
| method_required | 官网核对 / 电话 / 现场走查 |
| status | open / verified / rejected / expired |
| reviewer / verified_at / note | 与 city-facts/1 核验元数据对齐 |

约束：核验通过的事实仍须走 city-facts/1 导入包更新 `validation_status=verified` 及核验五元组（人、方法、时间、有效区间）；台账只记录任务状态，不直接改运行库。

## 4. 统一采集 Schema 与 city-facts/1 映射

三层结构，逐层可追溯：

1. **原始层（raw）**：`data/collection-runs/<run_id>/raw/` 存原始网页/快照/文件，SHA-256 入 manifest。内容不可信，只作证据。
2. **结构化层（structured）**：采集器按统一采集 Schema 抽取为 JSON：
   - 公共字段：`record_id`、`category`、`source_keys[]`、`collected_at`、`extraction_method`、`confidence_note`、`conflicts[]`。
   - 类别字段：按第 2 节各类别的关键字段；**缺字段保持 null，不允许模型或脚本编造**。
   - 冲突处理：同一字段多来源不一致时全部保留原值进 `conflicts[]`，不静默选一个。
3. **city-facts/1 导入包（bundle）**：由 `collector/bundle.py` 从结构化层生成，schema_version=city-facts/1，ID 稳定（同一实体跨批次同 ID，版本递增）。映射规则：
   - 每条记录带 `source_ids` 回指来源登记；`rights_status` 默认 `internal_only` 直至确认。
   - 无核验证据的记录保持 `validation_status=unverified`；核验五元组只能由人工核验流程写入。
   - 生成后先 `python scripts/import_city_facts.py <bundle>`（默认 dry-run），报告 ready/issues，人工确认后才 `--apply`。

与既有 `data/collection-20261023-expanded/` 的关系：该包是一次性脚本产物，本轮起其 `build_expanded.py` 的抽取逻辑迁入 `collector/` 包并参数化，新增任务日志、失败登记与断点续跑；旧包不改动、不入库。

## 5. 分阶段验收标准

### 阶段一（已完成）：现状审计

`docs/reports/data-audit-20261023.md` 已交付。

### 阶段二：机制建设（执行线程本轮任务）

- `data/collection.db` 台账五表可用，建表脚本幂等。
- `collector/` 包：范围/类别配置、任务→运行→失败登记、结构化抽取、去重（名称+坐标近似+来源指纹）、bundle 生成。
- 三个 Codex Skill 落盘 `~/.codex/skills/`（采集执行、核验整理、质量报告），脚本与 collector 包共享同一实现，不复制逻辑。
- 数据质量报告生成器按 `docs/DATA_QUALITY_REPORT_SPEC.md` 输出 8 项指标。

### 阶段三：第一批真实数据闭环可用（验收门槛）

同时满足以下条件才算"闭环可用"：

1. **类别覆盖**：至少 3 个类别（如 景区、餐饮、夜市）各有结构化记录并生成 bundle。
2. **城区覆盖**：记录横跨至少 2 个城区（如 龙亭+鼓楼）。
3. **来源可追**：每条记录 source_ids 非空，来源在 source_registry 有登记且 raw 哈希可核对。
4. **核验状态真实**：`verified` 记录具备完整核验五元组；`unverified` 如实保留，不为凑数升级。
5. **可 dry-run 导入**：bundle 经 `import_city_facts.py` dry-run 通过 schema 与引用完整性校验（records 解析零错误）；ready 数量如实报告，缺口逐条列出。
6. **工程机制可证**：至少一条 collect_run 记录带完整产物、至少一次失败被登记并可说明原因、重复运行不产生重复事实（幂等）。
7. **无红线**：零虚构数据、零 AI 猜测补全、零绕过 WAF、零绕过 import_city_facts.py 的写入。

### 阶段四（后续，不在本轮）：增量更新与目标日核验

目标日确定后，按第 6 节频率重采并核验目标日场次/开放/供应。

## 6. 更新机制设计

| 数据类型 | 建议更新频率 | 变更识别策略 |
| --- | --- | --- |
| 景区/博物馆名录与地址 | 月度 + 目标日前必查 | 来源页面 SHA-256 变化 → 结构化 diff → 变更字段入 bundle 递增版本 |
| 开放规则/票务 | 目标日前 7 天与 1 天各一次 | 同上；business_date 逐日建档，不沿用旧日 |
| 节目目录与场次 | 目标日前 3 天与当天 | 目录 diff + 逐场次核对；取消立即标记失效 |
| 餐饮商家与供应 | 双周 + 目标日前 | 门店页哈希变化；供应关系须有可追溯证据才更新 |
| 夜市/夜游 | 每周 + 目标日前 | 官方公告优先；临时休市只影响对应日期 |
| 交通设施/线路 | 季度 + 线路调整公告触发 | 政府公告 diff；无实时 Key 不承诺班次 |
| 便民设施 | 季度 | 低优先级，按采集任务排队 |

通用规则：变更识别只产生"候选新版本"，是否生效仍走核验与 import 通道；冲突保留原值与证据，按适用日期与核验结果解决，不静默挑方便的值。过期/取消事实标记 `business_status=cancelled` 或缩窄 `effective_to`，不物理删除（版本历史保留）。

## 7. 提示词注入与不可信输入防护

- 采集器是纯代码抽取：CSS/XPath/正则规则写在 `collector/` 代码里，网页正文永不作为 LLM 指令上下文；如使用模型辅助抽取，网页内容以数据形式传入，系统提示固定且不包含网页文本，输出必须符合采集 Schema，不合 schema 即丢弃并记 parse_error。
- raw 层文件只读保存；review 页面对网页文本做 HTML 转义，不渲染原网页脚本。
- 任何来自网页的"指令样文本"（如"忽略上述规则"）视为普通字符串字段值处理。
- 不绕过 WAF/访问控制：403/429 登记为 waf_block，限流退避，等待人工决定。

## 8. 验收职责与流程

执行线程交付后，本规划线程负责验收：

1. 核对实际 `git diff` 与 `data/collection-runs/` 产物，不凭报告验收。
2. 复跑验证命令：台账初始化、去重幂等、bundle dry-run、质量报告生成。
3. 按阶段三门槛逐条给出通过/退回结论；退回写明具体问题、证据位置与返工要求。
