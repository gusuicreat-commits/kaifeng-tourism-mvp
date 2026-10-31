# 汴梁游记可靠行程规划引擎 V1

2026-10-21。本次按用户粘贴任务开发独立后端工具，首次规划与途中重规划共用一个 OR-Tools CP-SAT 引擎，不依赖 LLM、聊天上下文或前端完成情况。**已验证的是软件在明确标注合成资料下的约束行为，未验收真实开封导航。**

2026-10-23状态复核：前端素材篮/协商、V3助手与正式行程确认已接通，真实高德报价和参考问路已有分轮证据；正式交通时段准入、真实业务资料和现场验收仍待完成。完整现状见 [PROJECT_STATUS](PROJECT_STATUS.md)，不能继续把2026-10-21的未接线项当作现状。

## E12 多天交通报价复用（2026-10-31，待 Claude 验收）

`live_travel.enrich` 在一次多天补全过程中，按起终点坐标、交通方式和北京时间 `HH:MM` 缓存报价（含失败结果）；跨日期同一时刻复用，不同出发时刻分别请求。命中不等待请求间隔、不消耗请求额度；各日 Journey 的 identity、证据有效期和快照仍独立生成。15 秒时限或 60 次上限用完后仍遍历剩余路段和日期：缓存命中继续生成当天快照，未命中不再请求或等待。单日及未传缓存的 `_enrich_day` 保持原有请求数和 Journey 前缀，缓存不跨调用保存。模块 INFO 日志记录本次 `provider_requests`、`cache_hits` 与因预算跳过的 `budget_skipped`（按路段与交通方式计数），每日另记跳过数，不改变 API 契约。

验收入口为 `backend/tests/test_live_travel_multi_day_cache.py`：合成 5 地点加同一起终点，三天同一出发时刻原需 90 次报价（上限 60 时预览失败），复用后请求 30 次、命中 60 次，三天均有完整路段和活动。另覆盖不同出发时刻、北京时间归一、失败缓存、双交通方式、额度用满后命中、单日兼容及调用间隔离；E12-fix 增加请求耗时/等待触及时限、首日中途耗尽额度、单日 Journey 前缀回归，以及两天 6 地点首日超时后仍可行的 API 预览。只使用合成资料与假 provider，真实交通与现场仍待验收。

## B2.1 多天契约与只读边界

新增 `MultiDayRequirements(city-engine/2)`、`DayRequirements`、`TripGoal(allowed_dates/fixed_date)`、带日期的票务确认、`MultiDayConstraints` 及 `MultiDayItinerary(city-itinerary/2)`。每日结果保存原始需求和当前剩余需求、日内计划/历史/ongoing、日状态与问题；全程包含过夜边界、objectives、active/elapsed/travel/步行指标、费用摘要与最弱证据状态。每日 24 站、全程 30 目标；日内 60 候选上限保留在原 prepare 中。计算预算仍为全程默认 1 秒、上限 3 秒。

仍使用 `city_engine_routes` 原主键存一套多天 JSON，`route_version` 属于整趟旅行。`engine/current`、`engine/versions/{route_version}` 和 `read_snapshot` 按 schema 返回完整版本；V2 当前只读，显式 `stale=true` 与 `MULTI_DAY_VALIDATION_UNAVAILABLE`，不调用单天 validator 认证它。引擎导出保留原 requirements/result/itinerary/progress JSON；未知 schema 拒绝。

HTTP preview/replan-preview 接收显式 `city-engine/2` 分支，但返回 `MULTI_DAY_UNAVAILABLE`；确认遇 V2 候选、历史恢复遇 V2、旧单日请求指向 V2 旅行，同样拒绝。纯 `plan_trip/replan_trip` 返回 unavailable；`current_and_future`、`selected_future_day` 只定义模型，不执行。多天算法、事务写链路、日期语义和前端分别属于 B2.2–B2.5。契约由 `export_engine_contracts.py` 生成 [city-engine-v2](contracts/city-engine-v2.schema.json)；旧 V1 契约无变动。

## B2.2 有界分天纯引擎

`engine/multi_day.py` 接通纯 `plan_trip(MultiDayRequirements, context)`；单日请求仍直接走原 `prepare → solve`。按日期准备候选与交通快照，先分配日期受限的必选，再分配灵活必选和可选，保留至多 8 个状态，三种全程偏好累计至多尝试 8 组；日内失败可回退到其他分配，不删除必选。三种偏好用完整路线的收益和汇总指标比较，相同行程合并标签，不相加日内 solver 的内部权重。

准备、分配与日内求解共享一个 monotonic 全程限额（默认 1 秒，上限 3 秒）；剩余不足 0.05 秒不启动子求解。`metrics` 分别记录分配、各日/偏好求解、交通矩阵和独立全程校验耗时，限时或分配裁剪设置 `search_truncated`，未找到完整方案时返回 timeout 而非无解；已验证的完整结果可返回 feasible，但不声明全局最优。可另传工作流 `deadline`，最终校验超时则不发布候选。纯引擎无 Provider 调用，不能据求解限额承诺端到端响应时间。

`multi_day_validation.validate_multi_day` 对照原始全程需求重新准备日内候选并复核硬约束，另查全程必选覆盖、目标/场次唯一、日期/端点/票务作用域、过夜快照与连续性、进度不可注入、全程时间/步行/已知费用汇总及最弱证据。过夜费用尚无 B3/B4 的计价契约，始终保留费用未知，省钱仅比较已知费用。测试见 `backend/tests/test_city_engine.py`、`test_city_engine_travel.py`，合成基准可用 `pytest -s -k multi_day_synthetic_benchmark` 打印 1/2/3 天 × 5/15/30 目标的状态与实测耗时。尚未接通 API 预览确认、多天重排、日期语义与前端。

## 交付状态

| 能力 | 状态与证据 |
| --- | --- |
| 城市景点、餐饮、固定演出、返程联合规划 | 已实现并通过合成案例测试；节目多场、同地多场使用独立活动实例 |
| 硬约束与独立校验 | 已实现；每个可行输出及每次确认均校验 |
| 途中新增、删可选站、少走路、延误、换未锁定场次、提前返程 | 统一剩余目标接口已实现；锁定或固定目标不自动放宽 |
| 已完成、跳过和当前活动保护 | 已实现；当前活动默认保留，明确结束按跳过保留记录，不伪造实际完成时间 |
| 预览、确认、正式/历史版本、恢复候选、事务与并发 | 已实现；恢复需通过当前数据验证，生成新版本，不回滚删除台账 |
| 有方向交通与费用数据入口 | 已实现；缺时段覆盖、路径、边界或成本时显式降级 |
| 园内 NetworkX 适配器 | 已实现并测试；只接显式提供的核验有向边，不载入旧示意图作为真实路网 |
| 高德报价适配器 | 适配器已实现，真实报价证据见 [高德能力报告](AMAP_REAL_API_CAPABILITY_REPORT.md)；正式时段矩阵未自动准入 |
| 前端、小汴梁 Agent | 已有软件接线和合成验收，见 [V3](CITY_AGENT_V3.md)及[主版本验收](MAIN_REFERENCE_MERGE_ACCEPTANCE.md) |
| 真实城市数据与实走 | 尚未验收 |

## 复用与代码范围

复用 FastAPI/Pydantic/SQLite、`city/facts.py` 的业务事实、`city/schema.py` 的证据/交通/Stop、`city/state.py` 的属主/CAS/事务事件/幂等回执，以及 M7 会话、留存、导出与独立删除台账。旧单景区 `itinerary.py` 将同一地点限制为一次访问，不能直接承担同台多场，因此复用其 OR-Tools 依赖与约束建模经验，新增城市活动实例模型，未改写旧求解器。

| 新增模块 `backend/app/city/engine/` | 职责 |
| --- | --- |
| `models.py` | 请求、目标、结果、运营数据与预览/重规划/确认契约 |
| `context.py`、`prepare.py` | 服务端实体消解、日期/来源/权限/供应关系、营业/预约/票种、候选与缺口 |
| `travel.py` | 请求内矩阵、方向/模式/有效期、出入园转换、未知费用；没有零填充 |
| `network.py` | NetworkX 在提供的核验有向图上计算步行路径，不补逆向或断边 |
| `amap.py` | 默认关闭的高德 Web Service 报价适配、超时、一次有限重试、速率/总量限制、错误分类 |
| `solver.py` | 一套 CP-SAT 选择、顺序、方式和时间窗模型，限时输出可行 incumbent/失败状态 |
| `validation.py`、`tools.py` | 与目标函数分开的硬约束复核、统一首次/剩余规划、差异比较 |
| `storage.py`、`service.py`、`api.py` | 增量表、身份/版本、预览、确认、历史、恢复、导出与 HTTP 入口 |

新增脚本：`scripts/demo_city_engine.py`、`benchmark_city_engine.py`、`import_city_operations.py`、`export_engine_contracts.py`。新增契约为 [city-engine-v1.schema.json](contracts/city-engine-v1.schema.json)，合成 fixture 为 `backend/tests/fixtures/engine_synthetic.json`；三份 `test_city_engine*.py` 覆盖纯引擎、HTTP/M7、交通适配。

已有文件只做后端接线：`main.py` 注册路由；`city/storage.py` 增量初始化与候选清理；`city/state.py` 导出新增对象和路线版本说明；`city/api.py` 增加工具能力标志；`memory/api.py` 将关闭留存覆盖新增候选及派生回执。现有前端接口没有删除或改名。

2026-10-21交付时未建立可用 Git 基线；2026-10-23复核时已有 `.git` 与 `main`，但仍无首次提交。修改前源码指纹和副本保存在 `.backups/planning-engine-v1-20261021/`。期间 4 个前端文件发生其他编辑，未写入或回退它们；没有执行前端构建或重启用户正在使用的服务。

## 输入、数据流与职责

```mermaid
flowchart LR
  Request[结构化目标与时间/预算] --> Resolve[事实目录与运营快照消解]
  Resolve --> CP[同一 CP-SAT：首次/剩余目标]
  CP --> Check[独立硬约束复核]
  Check --> Preview[只保存候选和差异]
  Preview --> Confirm[确认：授权/版本/时效/进度再验]
  Confirm --> Commit[事务保存路线版本/城市状态/事件/回执]
  Commit --> History[当前读取/历史/导出/删除台账]
```

`Requirements` 包含业务日期、带时区的开始/结束、起终点、交通方式、目标、节奏、优化偏好、可选硬预算及明确的预约/票种确认。业务时间按 Asia/Shanghai，同一业务日；内部耗时整数秒、距离米、费用人民币分。

目标由稳定 `goal_id` 表示，`target_kind` 区分 place/session/program/food。`required` 是硬意愿，`importance` 是可选目标奖励。节目目标展开为实际公布的日期场次；菜品目标展开为有有效供应证据的餐饮地点。`fixed_place_id/fixed_session_id` 固定实现地点或场次，保留原菜品/节目语义，不能因重规划丢失供应依赖。最终城市 `Stop.stop_id` 使用目标 ID。

请求不得携带事实、owner、任意路径或“已经核验”标志。HTTP 服务仅从服务端事实表和运营表取资料，`synthetic` 开关只存在于内部 `PlanningContext`，不是 HTTP 参数。游客直接报告的当前地点、票务确认和费用仍属于用户输入，不能称为独立现场监测或支付记录。

输出包括活动/场次身份、到达/开始/离开、停留和等待、必选/可调、证据引用、完整转场与返程、步行距离、时间及费用汇总、警告、未满足条件、求解耗时/候选数、差异和计算时刻。`navigation_certified=false` 始终保留；`feasible` 只表示依据当前资料满足模型约束，不保证现场到达。

## 算法与边界

候选筛选 → 适用方向/方式的保守交通矩阵 → CP-SAT 单路径 circuit、可选自环和时间窗 → 各目标独立求解与校验。首次与重规划共用准备后的 PlanningContext、交通矩阵和约束模型；高德估时仍只在求解前补齐一次。

2026-10-26 A2：默认按均衡 `balanced`、少走路 `less_walking`、省钱 `lower_cost` 各求一套，`alternatives` 默认 3、可限制为 1–3 个目标。用户指定的目标先求解并作为第 0 套主方案；已有 `less_waiting` 偏好替代均衡主目标，另外比较少走路和省钱。每套携带 `objectives`；活动、时刻、交通及费用等输出完全相同时合并标签，仍先校验每次求解结果。若合并后少于请求数量，则用第 0 套主目标继续求解并排除已有弧组合，补足的方案 `objectives=()`，仍逐套校验；各目标与补充求解共享 `solve_seconds` 总时限，超时或无更多弧组合时如实返回较少方案。确认索引及 `alternative_changes` 对应合并后的顺序。素材篮与小汴梁预览显示中文标签，备选标签附带与方案 1 可由行程数据计算的差异；省钱方案 `cost_unknown=true` 时注明“费用资料不全，只按已知费用比较”。合成覆盖见 `backend/tests/test_city_engine.py`、`test_city_engine_api.py`，报价复用替身验证见 `test_mvp_reference.py`；真实路线与浏览器验收由 Claude 另行执行。

硬约束包括：必选目标各取一个候选、同一场次不重复、完整停留在营业和目标时间窗内、固定场次与更早入场截止、转场/出入园耗时、返程时刻、预约/票种确认、再入园、已完成前缀和足以核算的硬预算。缺规则不默认开放；未知再入园只允许不需要再次进入的方案。每个跨景区转场必须有匹配核验入口关系及单独转换耗时，零转换也需显式证据。

软目标采用可解释的分层权重：目标奖励先于全部有界次级代价；次级代价综合交通秒数、步行米数换算惩罚、费用偏好、紧凑衔接、结束/开始时刻及改动幅度。每个可选目标奖励为 `1+importance`；少走路模式每米额外 4 个惩罚单位，平衡模式每米约 0.5；低费用模式每分额外 2 个单位。原计划相邻关系被破坏额外 120 单位。参数为工程设定，不是训练结果或游客收益验证。

节奏通过软性衔接余量偏好表达：轻松/标准/紧凑分别偏好 600/180/0 秒额外余量，不强制偷偷延长时间预算。用户未给的游览/用餐/休息时长采用明确记录的 1800/2700/900 秒参数；演出时长始终取有效事实。缓冲默认 300 秒，非排队实测。

请求最多 30 个目标，场次/商家展开最多 60 个活动、12000 条方式弧；受现有城市 Stop 契约限制，整份行程最多保存 24 个活动（含历史及当前保留项）。求解预算默认 1 秒、可配 0.05–3 秒，最多 3 个备选共享总预算。时间上限不包含进程首次导入依赖及输入准备开销。`OPTIMAL` 也只相对所给候选、矩阵及目标函数，不宣称真实城市全局最优。

## 交通与费用

正式引擎使用 `OperationalBundle` 中的有方向 Journey 和 PlaceCost。Journey 包含上下界秒数、距离、模式、路径依据、坐标系、来源/核对与有效区间、费用区间及出入园边界；边界另有费用上界，未知不视作免费。当前采用静态保守时段包络，交通和规则须覆盖本次规划时段；不把某时刻的即时估时无限外推。

`AmapProvider` 按官方 Web Service 文档实现步行/驾车/公交报价解析，但报价只是来源估计，没有经校准的全天上界，**不会自动喂入正式矩阵**。它需显式构造 `enabled=True`、已审查适用条款和服务端 Key；默认不发送网络请求。兼容入口仍默认 v3，超时默认 2 秒、最多一次有限重试、默认每秒 3 次/每实例 30 次上限。2026-10-22 M4 增加显式 v5 和有限 POI 查询，复用同一预算；独立验收脚本使用零重试、合计最多20次、默认仅 Mock。Web 服务 Key 已在本机配置，真实测试累计20次（18次业务成功、2次超时），地点、步行、驾车和公交已返回有效数据；纯内存实验用真实报价数值及合成业务条件完成三模式CP-SAT适配。正式Provider仍关闭，正式证据检查仍为needs_data，不能算真实旅游路线已经验收。运行方式、公交接驳步行限制和许可待确认项见 [M4.0报告](AMAP_REAL_API_CAPABILITY_REPORT.md)。

不持久化或缓存高德原始响应。商业授权、使用及存储范围须结合实际产品核实，依据[高德协议第7.3条](https://lbs.amap.com/pages/terms/)；本次不声明已经取得授权或验证真实城市/园内覆盖。当前公开运行库没有正式交通包，真实服务接通及时间变化校准仍待后续。

硬预算按费用上界核对；未知费用在无硬预算时允许规划但 `cost_unknown=true`，有硬预算则要求补数据。途中不能重置预算：有硬预算时须提供 `spent_cost_fen` 和 15 分钟内的 `spend_as_of`，包括先前完成活动。它明确标为 `user_reported`；当前活动还保守预留其完整已知费用，可能偏高，未知则不认证硬预算。总已知费用包含历史报告和剩余上界；不冒充实际支付账单。

## 途中保护与版本

重规划要求 ACTIVE/PAUSED、当前服务器时刻和 15 分钟内手动确认的有效地点；不接受客户端假造“现在”，不从原 gate 或酒店重启。默认预留 120 秒确认缓冲，记入剩余时间；候选第一段出发时刻已过则必须重新预览。`total_seconds`/`travel_seconds` 描述本次剩余规划区间，完整历史在 `completed_prefix`，当前保留活动单列 `ongoing`，不能把剩余耗时写成全天实际耗时。

已完成/跳过的前缀不进入决策变量，不能重排或重用其目标 ID。当前活动默认按原计划估计保留，标为 `prior_plan_estimate`；用户可明确报告剩余时长或明确结束。结束只记为跳过，不编造实际起止时间。已超预计结束但尚未报告完成的保留活动要求重新报告进度。

锁定未来活动默认保持身份、相对顺序和已确认时间。`fixed_goal_ids` 可额外固定未锁定活动。无法保留时返回冲突；若用户确实要放宽必选/更换锁定场次，先使用现有城市变更预览/确认明确解除，再调用重规划，不允许工具暗中删除。非锁定目标可删、替换或调整顺序。未处理目标为空时可只计算返程。

预览仅写候选及幂等回执，正式路线不变；计算在写事务外。确认时重查会话/独立台账、城市版本、当前进度、事实/运营完整指纹、候选时效、出发时刻和硬约束；单事务保存不可变路线版本、城市草稿及 route_version、最小事件和确认回执。重复 request_id 仅在内容一致时重放；并发只允许当前版本提交。数据库失败回滚；真实取消/过期造成旧方案失效时，读取返回 stale，不能因新规划失败又把旧方案称为有效。

历史恢复通过 `restore_route_version` 生成新的重规划候选，仍服从当前锚点、已执行前缀、锁定目标、当前规则及预算；确认后新增版本，历史保留，绝不直接回滚版本或删除台账。首版仅限同日且行程仍可继续时恢复。

## 规划工具与 HTTP

逻辑函数位于 `engine/tools.py`、`validation.py` 与 `service.py`：

- `plan_trip(requirements, context)`：纯计算，服务端准备 context；Agent 不应提交任意事实 context。
- `validate_itinerary(problem, itinerary)`：独立纯校验，problem 由事实解析器生成；确认与正式读取自动调用。
- `replan_trip(...)`：复用同一求解器，保护已完成前缀和当前活动。
- `compare_itineraries(original, candidate)`：保留/移除/新增/重排、地点/场次替换、时间和交通变化。
- `service.confirm_itinerary(session, trip_id, body)`：唯一正式路线提交门禁。

前端与 Agent 通过授权适配使用下列 HTTP 工具，不直连 SQL、不自行调用纯函数跳过 M7。所有私有读取/写入继续采用已有 `/api/v1/sessions` HttpOnly cookie 与同源边界。

前缀：`/api/city/v1/trips/{trip_id}/engine`

| 方法/路径 | 契约和行为 |
| --- | --- |
| `POST /preview` | PreviewRequest：request_id、expected_version、requirements；首次候选与必要备选，不提交 |
| `POST /replan-preview` | ReplanRequest：上述字段及 ongoing/end_ongoing、fixed_goal_ids、已支出费用或 restore_route_version；服务端消解当前时间/地点 |
| `POST /confirm` | ConfirmRequest：request_id、expected_version、candidate_id、alternative_index（从0计） |
| `GET /current` | 当前正式版本和独立核验后的 stale/issues，无正式路线返回 itinerary=null |
| `GET /versions/{version}` | 属主范围内历史快照；历史可读不等于当前可执行 |

返回 `feasible`、`infeasible`、`needs_data`、`timeout`、`unavailable` 或 `invalid`；成功候选外层 status=preview、must_confirm=true。失败没有 candidate_id，issues 含 code/message/affected_ids/suggestions；超时不算数学证明不可行。差异分别附在 changes 和 alternative_changes，确认哪个备选就核对哪个差异。可通过 OpenAPI 和机器契约注册工具，不需实现提示词或 AI Agent。

## 运行与导入

完全无 AI、无 DB、无网络的合成演示：

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp
.venv/bin/python scripts/demo_city_engine.py
```

输出 evidence_mode=synthetic，fixture 明确为虚构，不导入运行库。真实事实仍通过已有 `import_city_facts.py`；交通/成本包只能由维护者整理、核验后导入：

```bash
.venv/bin/python scripts/import_city_operations.py /absolute/path/to/operations.json \
  --database /absolute/path/to/database.sqlite3
# 默认为内存副本 dry-run；核验真实资料后才显式加 --apply
.venv/bin/python scripts/export_engine_contracts.py --check
.venv/bin/pytest -q
.venv/bin/python scripts/benchmark_city_engine.py --output /private/tmp/city-engine-benchmark.json
```

运营导入校验格式、地点引用、唯一 ID 与逐版递增，整批失败不写；不启动应用、不调用地图。增量数据表为 `city_engine_operations`、`city_engine_operation_revisions`、`city_engine_candidates`、`city_engine_routes`。前两者是业务资料；后两者 FK 到现有城市行程，随会话/行程及结束72小时清理级联删除。过期候选清理、导出、关闭对话留存清除候选及派生预览回执、备份恢复删除门禁均覆盖新对象。

本次仅在运行库副本验证增量迁移，旧行完全不变，见[迁移记录](reports/city-engine-migration-20261021.json)。未把 fixture、费用、道路或场次写入实际运行库，未重启8017；用户前端工作完成后按既有方式正常重启后端即可注册新路由，不需要删除数据库。

## 测试、性能和限制

最新自动测试结果与源码范围见[交付检查](reports/city-engine-delivery-20261021.json)。覆盖首次游览/餐饮/多场次、时间窗冲突、取消/缺结束、预约/坐标/供应缺口、返程与硬预算、不同偏好、再入园和边界费用；途中历史/当前活动、增餐/删站/少走路、锁定目标、失败保留、真实并发、事务回滚、幂等、事实变更/过期、历史恢复、导出/删除/旧备份门禁，以及导入器原子性和高德替身故障。小规模枚举对照验证了一个可手算的最短步行顺序；并未据此宣称所有实例均最优。

[本机合成基准](reports/city-engine-benchmark-20261021.json)：10/20/30 个候选均返回 FEASIBLE，选中10/20/24个；墙钟约1346/1006/1013ms，求解默认1秒。每规模单次测量，首次含依赖导入；不是 P95、生产 SLA 或现场性能。

尚未完成真实景区入口/通行、商家和供应、完整场次、跨场景走查；高德报价到正式可靠时段包络的适配/校准、正式业务使用边界与园内覆盖亦未验收；真实报价已单独验证。当前对多营业区间、未来交通变化、按票种精细计费、多团及跨日等保守拒绝或不支持；没有实时客流、预测排队、ML训练或新增 Agent 框架。

下一步应先核验一条有限真实链，导入后用相同工具复跑，记录实际到达偏差，复验已接通的前端与 Agent。算法实验可比较必选顺序基线与 CP-SAT，在相同事实和预算下记录硬约束通过率、目标满足、步行/等待、修改幅度、运行时间；合成算法对照与真实游客体验分开报告。

## 依赖与许可

本次未新增依赖，未复制 HTS-RPR 等研究实现。实际安装：OR-Tools 9.15.6755（Apache-2.0）、NetworkX 3.7（BSD-3-Clause）、Pydantic 2.14.1/FastAPI 0.148.0（MIT）、httpx 0.28.1（BSD-3-Clause）；版本与本地 LICENSE 已核对，原许可记录见 [THIRD_PARTY.md](THIRD_PARTY.md)。不引入商业求解器。
