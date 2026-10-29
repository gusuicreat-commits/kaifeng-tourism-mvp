# 多天行程设计（B1）

核对基线：2026-10-26，`codex/B1`。本文是供 Claude 审核、B2/B3/B4 实施的设计，不表示多天能力已经实现；行号指本 worktree 当前源码。B1 只新增本文，不修改任务表、代码、测试或契约。产品扩展依据为 `PLAN_VISION_GAP.md` 的 B 组任务；PRD 原有单日边界通过这组任务逐步扩展。

## 1. 推荐方案与交付边界

采用**一趟旅行、一份包含多个日程的正式路线、一个全程 `route_version`**。在单日 `prepare → solver → validate_itinerary` 外增加确定性分天编排层；不直接把单日时间轴延长为 48/72 小时，不引入第二个 Agent。日程之间用明确的过夜边界连接，夜间空白不算步行、等待或游玩时长。

首轮建议支持连续 1–3 个自然日、同一组游客共同行动、每天一个不跨零点的游玩时间窗；全程最多 30 个目标、每日至多 24 个已选活动，日内展开候选仍最多 60 个。重复到同一地点可以，但须是不同的目标实例。暂不支持跨午夜活动、跨城、自动酒店预订、多团分流或自动决定每天的起床/返程时刻。上限是待审核的工程建议，不是已有性能结论。

“周末玩两天”先生成日期范围和两个未完整的日程需求，再追问时间窗、起终点及过夜地点；信息不足时不出可确认路线。B2 可使用游客明确选择的已有地点作为过夜点，B3 才桥接酒店候选。这样 B2 可独立验收，不必假设已有酒店数据。

完整链路仍是：素材篮与对话表达意向 → 逐字段证据校验 → 保存规划条件 → 检查各日事实/交通与跨夜边界 → 分天求解 → 全程独立复核 → 展示全程候选及变化 → 游客确认 → 原子提交一个新版本。失败或取消保留原正式路线。

## 2. 现状盘点：单天假设及处理位置

以下按可执行链路列出检索到的单天模型、投影和消费位置。标为“保留”的地方仍需纳入回归，不代表要把所有日期字段改成日期范围。

### 2.1 契约、条件与事实

| 当前位置（文件:行号） | 当前行为 | 多天后的处理 |
| --- | --- | --- |
| `backend/app/city/engine/models.py:35–60` | `Requirements` 只有一个 `business_date`、起终点、时间窗；第 56 行强制上海时区同日；目标最多 30；`solve_seconds` 默认 1、上限 3 | 保留为单日求解输入；新增全程需求模型，按日构造此输入，不能删除同日校验了事 |
| `backend/app/city/engine/models.py:9–31` | `Goal` 没有可安排日期；仅有绝对时间窗、固定地点/场次 | 全程目标加 `allowed_dates` 和可选 `fixed_date`；旧字段仍描述日内约束，日期限制取交集 |
| `backend/app/city/engine/models.py:145–162` | `Itinerary` 扁平活动/交通，单个返程时刻、`completed_prefix`、`ongoing`、费用汇总及 A2 `objectives` | 新全程结果包含 `days[]`，日内继续复用这些值；全程汇总单独定义，不能直接拼接 `legs` |
| `backend/app/city/engine/models.py:174–200` | 预览和重排请求固定引用单日需求；重排仅有一个正在进行项、费用时点和固定目标集合 | 新请求带日程选择及修改范围；确认仍选择一套完整候选，不按天分别提交 |
| `backend/app/city/state_schema.py:9–39` | 草稿单日、24 站、全局连续已执行前缀 | 新草稿按日保存停靠与进度，按日验证前缀，全程校验 ID/场次唯一 |
| `backend/app/city/schema.py:90–130` | 架构契约 `CityTrip` 同日、最多 24 站，并不等于当前状态表 | 保留 `city-trip/1`；新增多天域契约，不把此设计模型误当实际存储 |
| `backend/app/city/exploration.py:22–48,89–137` | `Constraints` 只有一个日期；素材篮目标组成单个 `Requirements`；报错要求“同日” | 增加多天条件分支，按日检查缺项、全程组装目标；仍保留旧请求分支 |
| `backend/app/city/exploration.py:147–150,211–214,297–300` | 名称表遍历扁平活动；“返程”把时刻拼到唯一业务日；详情查演出也用该日 | 名称表遍历各日；返程变更必须绑定具体日期；演出按所问日期/选中日查询 |
| `backend/app/city/exploration_state.py:7–17,26–34` | 工作区 JSON 可存条件，但读取旧回执没有多天归一化 | 增加版本识别和只读归一化；保留“缺字段继承、显式 null 清空”的语义 |
| `backend/app/city/basket.py:98–117` | 素材“已安排”通过 `draft['stops']` 扁平比对，菜品供应用当前时刻判断 | 只读遍历各日停靠、附 day_id 与 stop_id；按实际安排日期核对供应，不让第二天的安排被标为未安排；素材对象仍只存一份 |
| `backend/app/city/facts.py:95–117,148–164,275` | `OpenRule` 和 `ShowSession` 严格属于一天；跨日营业要求拆分，跨日场次不支持 | **保留**：按每个日程的 `business_date` 查事实；住宿间隙不伪装成跨日演出/营业规则 |
| `backend/app/city/engine/context.py:35–55,73–97` | 有效性针对指定时刻，规则按地点和单日筛选；演示模式有明确的营业假设 | 日内分别调用；禁止拿全程起终时刻去判断单日规则，也禁止复制第一天规则到第二天 |
| `backend/app/city/location_resolution.py:48–65,284–324` | 样例接驳评估明确只接受一天并调用单日引擎 | **保留**该独立只读样例范围；不得把通过它称为多天行程通过 |

### 2.2 求解、确认与状态

| 当前位置（文件:行号） | 当前行为 | 多天后的处理 |
| --- | --- | --- |
| `backend/app/city/engine/prepare.py:22–41,57–100,109–126` | 相对一天开始计算秒数，节目只展开该日场次，营业/供应/费用覆盖该时间窗；必选缺数据立即报错 | 为每个“目标、日期”建立可用候选，分配后再日内 prepare；未分给某天的全程必选不能传成该天必选 |
| `backend/app/city/engine/solver.py:33–58,93–112` | 一个起点/终点的 circuit；每目标选一次；再入园、步行、预算是一个日程约束 | 保留日内 circuit；外层保证全程目标唯一、必选覆盖和按日步行限制；全程预算由外层校验/协调 |
| `backend/app/city/engine/solver.py:121–168,174–201` | 单份时长/费用；三目标共享一次 `solve_seconds`，完整相同结果合并标签；未知/超时有不同状态 | 全程三套方案按相同目标贯穿各天；所有分配尝试和各日子求解共享总求解限额，不能按天乘三再乘备选 |
| `backend/app/city/engine/context.py:128–142`、`engine/tools.py:12–27` | `max_selected=24`；重排从 24 减全局历史数；复制一个已完成前缀与 ongoing | 改为每日日内上限、全程目标上限；历史/进行中只交给相应日，已结束日不参与求解 |
| `backend/app/city/engine/travel.py:21–76,90–113` | 矩阵及出入口依据覆盖单个日程时间窗，查单日营业规则 | 按日构建/复用请求内快照；不能用一份第一天矩阵覆盖全程 |
| `backend/app/city/engine/live_travel.py:27–42,78–95,117–145` | 只展开该日场次，最多 8 地点；估时 ID 只按两端/方式生成，恢复时以 ID 去重；报价 15 分钟到期 | 保留日内 8 地点上限；快照身份加入日期/时间窗及报价修订，避免不同日同一条边相互覆盖；共用请求/时间预算 |
| `backend/app/city/engine/validation.py:8–79,82–99` | 校验一条连续路线 `legs=activities+1`、单日必选、返程、预算；差异不含跨天移动 | 每日独立复核后再全程复核；增加目标归属、过夜连续性、全程费用和 `day_changes` 比较 |
| `backend/app/city/engine/storage.py:18–29,66–68` | 候选和路线是整趟的 JSON 快照，主键 `(trip_id,route_version)` | 继续一版本一整份快照；JSON 用 schema 区分新旧，沿用关联删除/导出 |
| `backend/app/city/engine/service.py:34–37,44–125` | 全局前缀、未来锁定、单个 ongoing；重排业务日必须等于草稿业务日；起点和 now 由服务端控制 | 按日比较进度；取上海“今天”的日程；保持新鲜手动锚点、锁定、正在进行保护，不使用酒店计划冒充当前位置 |
| `backend/app/city/engine/service.py:129–190` | 读取单日 JSON；历史恢复与 now 覆写仍须通过同日校验；已支出只算一次当前路线 | 按 schema 分派；恢复只恢复未冻结部分；已支出基线改为全程去重账本（B4） |
| `backend/app/city/engine/service.py:197–214,218–280` | 候选绑定状态/篮子/工作区/事实基线；确认检查所有报价、首段时刻，再构造一份单日草稿并 `route_version+1` | 全程一次确认、一次事务；额外绑定酒店服务工作区版本/绑定修订；检查每个可变日首段和跨夜边界，冻结日不重新要求出发在未来 |
| `backend/app/city/engine/service.py:289–306` | 读取时用单日 prepare 复核；全局 stale 与交通刷新标记 | 增加日级 issues/stale/refresh，冻结历史和未来失效分开呈现；全局版本冲突仍使修改候选失效 |
| `backend/app/city/state.py:37–83,137–181` | 端点和所有停靠共享日期、起止时段；完成/跳过只能处理全局第一 pending；日期/开始时间有历史后不可改 | 日内前缀、日级冻结；已开始/冻结日日期和原始起点不可改；当天未执行变更继续走确认门 |
| `backend/app/city/state.py:258–279`、`city/storage.py:11–32,45–51` | 导出草稿/候选/路线/工作区；整个 trip 终态触发留存清理 | 导出包含全部日及过夜/账本；结束一天不能把整趟 trip 设为 COMPLETED 或开始 72h 清理 |

### 2.3 小汴梁、住宿与前端消费

| 当前位置（文件:行号） | 当前行为 | 多天后的处理 |
| --- | --- | --- |
| `backend/app/city/agent/models.py:54–83` | `PlanningField` 只有单个 date/time/origin/destination、一个预算，重复字段没有日期作用域 | 增加范围/天晚数及日字段作用域；证据与类型校验仍在服务端 |
| `backend/app/city/agent/planning_intent.py:12–64,67–108,194–211` | 日期解析单值；`trip_days` 仅识别天数并提示限制；时间拼同一天；每人预算被单独丢弃 | 增加范围解析/交叉校验和按日 patch；B4 才启用按人预算计算，不能提前去掉保护 |
| `backend/app/city/agent/orchestration.py:14–20,53–89` | 只遍历第一套扁平活动；缺项追问不带日；明确提示“只能先排第一天”“按人预算暂不能用” | 按日汇总遗漏和追问；B2/B4 对应闭环验收后分别替换提示，不能只改文案 |
| `backend/app/city/agent/understand.py:254,291–294,411–412` | 字段归一化与模型提示只描述单日字段；入住日期禁止混入游玩规划 | 更新白名单、归一化和提示；酒店与游玩字段仍分域，用显式过夜绑定连接 |
| `backend/app/city/agent/requirement_context.py:6–13,23–56` | 共用需求投影仍是单日缺项；酒店仅投影候选 id/name | 投影全程与聚焦日，标明缺项所属日；酒店候选仍是意向，另投影受控绑定状态 |
| `backend/app/city/assistant/context.py:86–107,125–178,189–215`、`assistant/models.py:74` | V3 也复用这份上下文；直接解析单日模型，候选用第一条 leg 判过期；第一 pending 是 next，第一天过去即 `DIFFERENT_BUSINESS_DATE` | 集中版本分派；候选检查可变日首段；新上下文带范围、聚焦日和各日状态，不能第二天把整趟路线判废 |
| `backend/app/city/agent/conversation.py:38,65–70`、`agent/service.py:349–356` | 模型投影压缩时 next 只留 ID/名称、不留日期；ReAct 预览名称表遍历扁平活动/草稿 | 压缩后保留 day_id/日期作用域；按日遍历名称及差异，不因 token 预算裁掉硬限制/日归属 |
| `backend/app/city/assistant/tools.py:16–42,45–100,109–120` | V3 复用的工具把草稿重新拼回单日需求，保护演出/下一活动无日期范围 | 统一读取全程语义，再向日内工具传明确日期切片；不能把第二天目标丢失在投影中 |
| `backend/app/city/agent/toolbelt.py:17–34,99–119,122–177` | 保护演出遍历全部未来站；餐厅查询、return_by、meal_at、交通复核使用一个业务日，固定 `v3:meal` | 全程保护保留，但日内工具只接聚焦日切片；用餐 ID 全程唯一；“第二天吃饭”只改相应日 |
| `backend/app/city/agent/semantic_execution.py:29–42`、`assistant/interpret.py:36–50,71–99`、`assistant/service.py:31–33,108,119–130` | 完成/返程时刻拼草稿日期；指代遍历全局未来站；下一站交通用唯一需求加 now | V3 状态操作绑定活动所属日，跨日同名指代补问；当前查询保持“现在”语义；旧 V2/离线路径不默认第一天 |
| `backend/app/city/agent/toolbelt.py:86`、`city/weather.py:302–351` | 餐饮回复写“今天”；天气已按活动时间覆盖但回复只写 HH:MM | 查询标明实际日期；天气保留覆盖未知，并展示日期，不新增天气服务 |
| `backend/app/city/travel_services.py:17–34,52–71,309–365` | 单份服务工作区已含住宿日期与 selection.hotels；确认只是意向；阻塞文案明确多日未接 | 扩展为按住宿区间引用的绑定；服务选择/撤销不自动改路线，下一次路线预览再应用 |
| `backend/app/city/hotels.py:30–55,67–84`、`providers/adapters.py:34–54` | FlyAI 只有地址/起价等参考，没有坐标，`total_fen=None` | 不从地址推坐标，不用起价乘晚数冒充总费用；需明确地点绑定和单独费用依据 |
| `frontend/src/components/TravelServiceCards.vue:30–35,53–58` | 已能展示入住/退房及晚数，但明确只确认候选意向，完整预算未知 | 保留候选语义；增加所覆盖夜晚、地点绑定和接续预览入口，预算状态接 B4，不能仅改“已落实”文案 |
| `backend/app/city/location_resolution.py:204–227` | 高德候选确认仅经维护绑定映射正式事实，无绑定则 needs_data | B3 沿用这一事实门，不把同名/相近 POI 自动当酒店入口 |
| `frontend/src/explorationApi.ts:4–25`、`cityApi.ts:9–14,33–34` | TS 条件/草稿/路线都是单日，差异比较仅时分 | 增加新旧判别联合与按日视图；所有跨天差异带日期 |
| `frontend/src/components/BasketPlanning.vue:14,40–59,94` | 一个日期/起止时刻、一组端点；预览活动和费用扁平 | 日卡片编辑/预览，整套方案一次确认；遗漏按全程并集判断 |
| `frontend/src/components/CityAgentChat.vue:164` | 对话候选遍历第一套扁平活动 | 共用按日预览组件，避免出现“聊两天、只显示第一天” |
| `frontend/src/pages/JourneyPage.vue:17–31,43–51,63–81`、`components/ItineraryFlow.vue:22–46` | 唯一日期编辑、总站数、同日场次、一个起终点/时间轴和下一站 | 页头范围＋按日切换；Flow 接收某一天切片；编辑/完成动作绑定日与全程版本 |
| `frontend/src/journey.ts:25,69–77`、`amap.ts:9–21`、`components/CityMap.vue:9–16` | 全局第一 pending、一个 returns_at 判断有效性；地图使用整份活动列表 | 共用当前/选中日投影，地图 revision 含 day_id；不能画跨夜连线或把明天标成今天下一站 |
| `frontend/src/pages/Home.vue:18–29`、`pages/MyPage.vue:11,19` | 旅行入口/历史卡片用单个日期和扁平停靠判断有内容 | 用范围和全程站数；首页当天无剩余时提示“今日结束/明日安排” |
| `frontend/src/pages/CityTrip.vue:21–36,77–87`、`pages/CityPlanner.vue:16–25,44` | 保留的管理页/表单规划器直接读写单日 | 旧单日继续可用；多天管理页至少支持确认当前位置及按日进度，旧表单规划器遇多天转主流程，禁止写回单日覆盖 |
| `docs/contracts/city-engine-v1.schema.json:257,665,1193`、`city-trip-v1.schema.json:184`、`city-assistant-v2.schema.json:200`、`city-agent-v3.schema.json:346` | 提交契约包含上述单日形状；JSON Schema 本身不体现全部 model_validator 约束 | 实施时由 exporter 生成新模型契约并回归旧契约，不能手改 JSON 或仅靠 schema 判断同日规则 |
| `backend/tests/test_planning_understanding.py:388–447`、`test_city_engine.py:20–24,190–204` | 已测试周日“周末”取当日、只存第一天、拒绝按人预算；A2 共享时限已有测试 | 保留单日周末行为；仅替换多天/预算限制对应断言，新增范围、跨日保护与总时限测试 |

`timing.py` 使用绝对时间评估两活动衔接，本身没有旅行必须同日的约束；无需扩成多天求解器。旧园内路线、采集日程库、两点参考问路也不因此获得多天正式规划能力。

## 3. 数据模型、存储与迁移

### 3.1 全程需求与每日需求

建议新增 `MultiDayRequirements`（`schema_version='city-engine/2'`），保留现有 `Requirements` 作为单日内部模型。新字段的最小含义如下；结构化模型名为设计建议，实施时保持周围命名风格。

| 层次 | 字段 | 约束/含义 |
| --- | --- | --- |
| 全程 | `start_date`, `end_date`, `timezone='Asia/Shanghai'` | 两端均包含；`day_count=(end-start).days+1` 由服务端导出；连续 1–3 日 |
| 全程 | `days: tuple[DayRequirements,...]` | 顺序、日期与范围一致且无重叠/遗漏；`day_id='day:YYYY-MM-DD'` 服务端稳定生成，不使用数组下标做身份 |
| 每日 | `business_date`, `starts_at`, `ends_at`, `origin_place_id`, `destination_place_id` | 同一自然日、有时区、end>start；每一天独立必填；第一天起点、最后一天终点不可从酒店自动覆盖 |
| 每日 | `origin_text`, `destination_text`（仅条件/草稿） | 未解析意向；不能直接送 solver；日间缺项保留 null，不从第一天无声复制 |
| 全程 | `goals: tuple[TripGoal,...]` | 复用 Goal 语义，加 `allowed_dates`, `fixed_date`；目标 ID 全程唯一，最多 30 |
| 全程 | `overnights: tuple[Overnight,...]` | 每对相邻日恰有一个边界，详见酒店节；必须连接两个已知端点才能确认全程路线 |
| 全程/每日 | `modes`, `pace`, `objective`, `access_confirmations`；日级 `max_walking_seconds` | 顶层为明确的共用条件，日级覆盖优先；预算不在此按日复制。步行硬上限按天，不把首日体力限额乘天数当全程偏好 |
| 全程 | `budget`（B4），`solve_seconds`, `alternatives` | 总预算是全程；求解时限和备选数也是全程。旧单日 `budget_fen` 的现有语义不变 |

部分规划条件模型 `MultiDayConstraints` 允许范围、每日字段缺省；准备完整引擎请求时才要求齐全。草稿用 `CityDraftV2(schema_version='city-draft/2', start_date, end_date, days, ...)`，每日保存原始起终点、时段及 `stops`。正式需求的目标表是事实/兴趣语义的来源，草稿停靠保存具体实现及进度；沿用现有按 goal_id 对账方式，不能只从停靠重建 program/food 语义。

目标分配规则：

- 跨天必选：`required=true` 且 `allowed_dates` 为多日，表示全程完成一次，不是每天一次。例如“这两天一定去开封府”。
- 指定日必选：`required=true, fixed_date=...`；`fixed_date` 必须属于 allowed_dates。固定场次还须与真实场次 business_date 一致。
- 每日可选：`required=false, allowed_dates=[该日]`；全程可选则列出多个日。重要程度沿用 importance，不自动把“必去”降为可选。
- “每天吃一顿午饭”展开为每日日实例（不同 goal_id）；去重按实例，不按地点名。“每天去同一店”不能被合成一个跨天目标。
- 已完成或跳过的实例不再分配；跳过必选仍须先明确解除锁定。跨天移动保留原 goal_id，不能通过生成新 ID 绕开锁定。
- `window_start/window_end` 是绝对时刻约束，与允许日期取交集；不得把第一天时刻机械换日。相对“每天 12 点”在证据校验后才展开为多个实例。

票种/预约确认也有日期作用域：现有 `access_confirmations` 只是地点 ID 集合，多天投影必须记录确认适用日或有证据的多日票范围，按日生成该集合。第一天确认持票不能无依据地复制到第二天，更不能把同日再入园规则当成次日票务资格。

### 3.2 正式路线与版本

新增 `MultiDayItinerary(schema_version='city-itinerary/2')`，包含 `days[]`、`overnights[]`、全程 `objectives`、费用摘要、证据模式和指标。每个 `DayItinerary` 保存 day_id/business_date、**原始每日需求**、日内计划/剩余计划、日内历史及 ongoing、状态和校验问题。原始开始与重排后剩余开始分开，不能用“当前 now”覆盖最初出发记录。

汇总明确区分：`active_seconds=Σ每日游玩及转场时长`、`elapsed_seconds=末日抵达-首日原始出发`（包含过夜间隙）、`travel_seconds=Σ日内转场`、`walking_distance_m=Σ日内步行`。不将 elapsed 当游玩时长；旧单日 total_seconds 仍保留原义。总证据状态取各日/过夜中最弱者，单日的 verified 不能覆盖另一日的 reference/unknown。

不采用每天单独一条正式 route：它会产生多组版本、跨天移动半成功和“今天已确认、明天还旧版”的问题。继续使用 `city_engine_routes` 的现有主键，一行存整套多天 JSON；候选也存整套结果。`route_version=0` 表示从未确认引擎路线；首次确认 +1，此后任何一日路线/过夜绑定/费用约束变更确认，都使整套 +1。`version` 仍是旅行聚合状态版本，记录进度和锚点变化；不增加可单独提交的 day_route_version。

预览及确认绑定：`trip.version`、当前 `route_version`、篮子版本、规划工作区版本、服务工作区版本（B3 起）、事实/运营指纹、日级冻结快照摘要、费用账本修订（B4 起）。复用原事务、幂等回执和 consumed；一个日失败整套回滚。候选 15 分钟 TTL 仍适用，还须受每段交通报价的更短有效期约束。冻结历史保留当时指纹，不要求今天的事实重新证明昨天已经发生的记录。

日级状态建议 `pending/active/closed`，另有只读 `frozen` 判定；这不替代全程 DRAFT/ACTIVE/PAUSED/终态。关闭某一天不触发整趟结束或删除时钟。闭日时若仍有 pending，先展示清单让游客补录实际完成、明确跳过或移往后续天；不得自动填实际时刻。自然日过去但未闭日时，界面标“历史进度待核对”，该日计划亦从可求解集合冻结，不能通过重排改写。

V2 状态操作增加明确 day_id；`close_day` 也走 expected_version、幂等和重要变更确认。`ReplanRequestV2` 除 scope 外带 `day_id`（selected_future_day 必填）、原有 ongoing/fixed_goal_ids/restore_route_version 及费用报告；current_day 的目标日由服务端核对，客户端传错不能改别天。所有已完成记录继续使用带日期的实际时刻，而非只有 HH:MM。

### 3.3 单天兼容与契约版本

1. 保留旧 `Requirements/CityDraft/Itinerary/CityTrip` 和旧单日请求、返回形状。新请求必须显式使用 `/2` schema；现有 `/api/city/v1/trips/...` URL 可接收判别联合（新分支需显式 tag，旧缺 tag 仍走旧分支），URL 的 v1 不用作 JSON schema 判别。`engine/current` 按该条路线 schema 返回形状，新前端先判别再读取。
2. 新多天草稿需显式 `city-draft/2`；没有标记的旧 draft/itinerary JSON 按旧模型解析。服务端内部可只读包装成一日视图，用原 business_date 生成 day_id，但不重写历史 JSON、不改旧哈希、回执、route_version，也不把旧快照补成有新证据的路线。
3. 旅行开始前，游客明确把单天扩为多天时，新增多天条件/预览；确认后写 V2 草稿及新 route_version。已执行的旧单天历史可以包装成不可变首日，追加未来日；不得改其日期、实际记录或重跑旧目标。
4. 表结构目前主要是 JSON，可先不改 route 表。`state.describe` 根据草稿标记输出 `city-state/1` 或 `city-state/2`；所有 `model_validate_json` 入口集中按 schema 分派，未知 schema 明确拒绝，不捕获异常后退回单天。旧前端不支持 V2 时显示升级/主流程入口，不进行有损写回。
5. 新响应仍由原 trip 拥有者读取；导出/删除/历史关闭覆盖每日对象、住宿绑定、费用记录与候选。新对象尽量嵌入 trip JSON；确需表时必须带原 trip 外键及 ON DELETE CASCADE，并加导出删除测试。
6. `export_engine_contracts.py`、`export_city_contracts.py` 增加 V2 导出；`export_agent_contracts.py` 更新 PlanningField/理解协议版本；`export_city_assistant_contracts.py` 对复用的 CityContext 增加范围/日信息。新增 `city-engine-v2.schema.json`、`city-trip-v2.schema.json`，旧文件继续由旧模型生成；共享上下文的契约若发生增量变化也必须重生。`city-facts-v1` 在 B2 不变，营业/场次仍按日。
7. 不存在独立 state exporter：当前 `CityDraft` 在 assistant exporter 中。实施时给 V2 草稿增加实际导出覆盖，不能只更新抽象 CityTrip 契约漏掉 HTTP 使用模型。新 agent 协议建议从 `travel-intent/3.3` 升为 `3.4` 并保留读取旧待办；不能让旧含日期的 proposal 重新解释成多天。

## 4. 算法：分配目标到各天，再求日内路线

### 4.1 方案比较

| 方案 | 规模与时限影响 | 解释与复用 | 取舍 |
| --- | --- | --- | --- |
| A. 有界分天搜索＋逐日 CP-SAT | 先建目标×日期可用性，再枚举少量分配；各日最多 60 候选、12000 弧现有门槛。重试消耗一个全程 solve_seconds，不是每次重新给满 | 可解释“该目标仅周日开放/周六已满”；高度复用 prepare/solver/validation、交通及锁定。分配搜索可能漏解，不能声明全局无解/最优 | **推荐首轮**，限制为 1–3 天，用稳定排序和有界回退解决贪心陷阱 |
| B. 一个多天 CP-SAT 联合模型 | 每日有独立起终点/circuit，目标在多日候选间选一次；弧约为 Σn_d²，若粗暴做全程 circuit 会接近 (Σn_d)² 并出现跨夜弧。预算/目标分配可联合优化，但变量、建模与独立复核都扩大 | 全程约束表达直接；需要重写活动索引、日期 circuit、夜间处理、锁定和验证，现有短时限下是否更好没有测量 | 后续若分天回退频繁再评估；不能只删同日校验后把原模型称联合求解 |

不采用“按篮子顺序塞满第一天，剩余扔第二天”作为唯一策略：第一天有时间的全程必选可能占掉仅第一天有场次的必选位置。也不能日内各自求三套再做无界笛卡尔积。

### 4.2 推荐实现步骤

1. **按日准备候选。** 在一致的服务端事实快照中，对每个允许的日期展开目标，保留候选、默认停留参数、阻塞原因及适用日。营业、票种/预约、供应和费用用该日时间窗；场次用自身日期。此阶段不要因某必选在周六不可用就宣布全程缺数据，要先检查其是否可在周日完成。
2. **锁定不可移动项。** 固定场次、fixed_date、显式固定未来活动先放入指定天；冻结/已处理目标移出决策集合。剩余必选按可用日期数升序、时间窗宽度、importance 和 goal_id 稳定排序。先放日期最受限的，再放灵活的；可选目标在必选后考虑。
3. **有界搜索分配。** 维护最多 8 个分配状态；按各日已占时长、候选地点间有依据的距离/交通、目标收益和当前目标偏好排序。未知交通不补零，只能作为尚待验证的分配，不能作为“更近”的证据。至多完整尝试 8 组分配；这些是服务端上限，需基准测试再定，不向 LLM 开放任意扩大。日内失败时尝试换日/交换两个灵活目标，而不是删除必选。可用轻量主问题剪枝，但不另造一个无界联合 solver。
4. **逐日求解。** 每次日内只传被分给该日的必选及可选目标，复用 `prepare/solve/validate_itinerary`。所有分给本日的 required 保持 true；每个全程实例至多落一天。空目标日允许检查起终点接驳（现有 req.goals=() 能算转场），两端同地也必须有可验证的原地衔接语义，不伪造缺失边。
5. **分配后修补与复核。** 若日内未选某可选，可尝试放到其他允许日，仍占同一限额；全程必选未全覆盖绝不输出可确认的“部分成功”。分别复核日内全部硬约束，再独立检查全程目标/场次唯一、跨夜同点、日期范围、锁定不变、冻结摘要、预算。问题带 day_id、goal_id 及原因，不能把两天混成“时间不够”。
6. **结果分类。** 必选所有允许日均缺事实时 `needs_data`；发现明确固定约束矛盾可报 `infeasible`；有界分配/日内限时搜索没找到整套可行解但未穷尽时 `timeout`，明确“不证明无解”。已有一套全部复核通过的结果则可返回 feasible，注明 search_truncated/非全局最优。不得把只算好第一天的候选交给全程确认。

### 4.3 `solve_seconds`、交通与三目标

保持单日请求默认 1 秒、范围 0.05–3 秒和原 A2 测试不变；多天请求也先沿用**全程默认 1 秒、上限 3 秒**作为计算阶段总限额（monotonic 墙钟，不是进程 CPU 时间），性能不足先返回 timeout 并记录，再由团队基于合成基准调整，不能默认为每一天给 3 秒。外层按目标阶段、分配尝试、未解天数分配剩余时限；日内可用 `objective=当前目标, alternatives=1` 避免重复三目标循环。剩余不足现有子请求 0.05 秒下限时停止，不上调凑最小值而超额。用可注入单调时钟测试累积配额。

总限额覆盖分天搜索、模型构建与 solver 的计算阶段；子调用中已发生的独立复核也消耗外层剩余时间，全程最终复核及确认另外计时，超外层工作流 deadline 不发布候选。分别返回 assignment_elapsed_ms、各日/各目标 solve_elapsed_ms、validation_elapsed_ms、travel_elapsed_ms 和 timeout_scope。Agent 总请求预算与交通网络预算是另外两道门；不得拿“求解 3 秒”承诺端到端 3 秒。

交通按 `(date, day_window, origin, destination, mode, quote_revision)` 区分派生快照；同一天的三目标共享矩阵，不重新调用 Provider。跨日同一地点对能否复用，须有证据明确覆盖各日窗口。现有高德实现最多 8 地点、每实例 max_requests=60，不能简单每一天创建一实例乘上请求量；B2 增加全程请求/截止控制，不在本任务调用外部服务。未来日期的查询仍只是参考估时，不能延长报价有效期伪装预测可靠性；出发当天可提示刷新，未刷新不自动改路线。

A2 的 `balanced/less_walking/lower_cost` 是**三套全程方案**的目标标签：

- 均衡：先满足全部硬目标，再最大化全程可选目标收益，随后比较日内时间/转场/等待/步行和改动惩罚；夜间睡眠空白不加等待惩罚。
- 少走路：同样先满足硬目标和目标收益，再比较全程 Σwalking_distance_m，同时执行每天步行硬上限；不是每天单独取最短再随意拼接。
- 省钱：比较全程唯一费用条目的已知上限；未知时只能标“仅按已知费用比较”，不能标预算已满足或最便宜。

每套完整候选按同一 objective 搜索分配并逐日求解；不要把“周六均衡＋周日省钱”标全程省钱。比较时不能直接相加各日 solver 中依赖候选规模的权重；外层用明确的全程收益和汇总指标排序。完整日程、日期、过夜边界及费用实现相同才合并 `objectives`，沿用 A2 去重原则。兼容用户显式 `less_waiting`，不强制替换成均衡。限时不足时可以少于三套；`objectives=()` 的额外备选只在有余量时生成，不为凑数重复整套。

## 5. 酒店过夜点（B3）

### 5.1 绑定与应用

`Overnight` 最小字段：`overnight_id`、`from_day_id`、`to_day_id`、`night_date`、`place_id`（未解决时为空）、`source_kind='selected_hotel'|'user_place'`、`service_selection_id`、`service_version`、`binding_revision`、`check_in/check_out`、地点/入住条件的证据状态。路线确认后保存所用绑定快照，不能随当前工作区 selection 变化而暗改。

流程分两次明确确认：现有服务确认只选择酒店意向；随后规划预览显示“10-31 结束于 A 酒店 / 11-01 从 A 酒店出发”，游客确认路线才正式采用。中途选另一家或撤销酒店意向只使相关规划预览失效，原正式路线保留并提示需重新计算。新路线确认同时验证 service_version/selection_id/绑定修订；现有 Agent guard 的服务版本检查要延伸到引擎直接预览与最终确认。

B3 首轮只支持**一家酒店覆盖所需住宿区间**，首日之前/末日之后额外住宿不自动加入游玩日期。同一酒店的意向可引用多个 night_date；未来换酒店时用多个 Overnight 扩展，不能覆盖单个 selection 后丢掉早前酒店。B2 的手动已知过夜点继续可用。酒店入住/退房日期与游玩范围不一致时列出差异追问，不能从“玩三天”自动声称已订两晚。

每个边界满足 `day[d].destination_place_id == overnight.place_id == day[d+1].origin_place_id`。前一天抵达与次日出发间隔是留宿间隙，不生成道路/连续交通 leg。若两端不同，不能画一条夜间直线；首轮追问统一到哪个过夜点，真实夜间转移另立后续任务。抵达不能晚于明确的入住截止，次日离开/退房限制如有来源则核对；未知入住规则明确“待向酒店核实”，不假定全天接待。更换起点或终点不得覆盖游客指定的车站/返程地点。

### 5.2 地点证据与降级

- FlyAI `Hotel` 当前无坐标，不能把酒店 id 当 place_id。用选中候选的身份和地址与已有正式/参考地点进行**受控绑定**；优先现有 `city_object_bindings` 机制扩展 `flyai_hotel` 类型，绑定由维护端导入，客户端和模型不能提交“已核验”引用。
- `Place.place_type` 当前没有 hotel 枚举（`facts.py:62`）；首轮复用合法 `poi` 地点作为端点，住宿身份来自 Overnight/服务绑定，不为酒店另造一套事实库，也不将它当普通游览目标加默认停留时长。
- 若需高德查位置，复用已有地点搜索及候选确认，显示名称、地址和 GCJ02；用户选同名候选只证明其意向，仍须维护绑定才能进入 facts 门。参考事实仅按现有 reference 准入规则使用，保留 reference/unverified、来源和有效期，不提升为 verified。
- 证据须记录 provider/外部 ID、来源定位、checked_at/有效区间、坐标系、绑定方法/修订；酒店 POI 中心不是前台入口。WGS84 只经已有受控转换，不把未知坐标系硬标 GCJ02。高德估时仍为 estimate，`navigation_certified=false`。
- 当前 `city_object_bindings` 仅有 kind/object_id/fact_kind/fact_id（`basket.py:48–51`），没有修订或证据字段。B3 需增量迁移维护绑定元数据或关联记录：旧绑定只读兼容且不补造证据，新酒店绑定必须有元数据；绑定指纹纳入候选基线，不能只在设计里引用不存在的 binding_revision。
- 无绑定/坐标/适用交通时返回 `HOTEL_LOCATION_PENDING` 等按日缺项，可继续看候选或改选已有地点，不能用酒店名自动推导航路线。B3 代码验收用临时库维护绑定和 Mock 数据；真实酒店绑定由队员采证，不自动导入运行库。
- 入住意向不是已订房，房型、人数容量、房态、价格和入口可通行分别标未知。坐标可用不等于已核实可入住；可以展示参考路线及待确认住宿条件，但不能宣称“已安排好住宿/保证入住”。

没有酒店且无手动过夜点时，按缺项问：“10 月 31 日晚上住哪里？可选已确认的酒店候选，也可选择已有地点作为过夜起终点。”酒店已选但地点未解决时问位置，不再问“选哪家”；酒店条件已齐则不重复问入住日期。只说“回酒店”，多天中默认指当前日对应过夜点，多个可能指向时补问，末日没有后续住宿时不得自动延长一晚。

## 6. 全程预算与按人预算（B4）

### 6.1 表示和换算

新增 `BudgetRequirement`：`amount_fen`、`basis='party_total'|'per_person'`、`period='whole_trip'`、`party_size`（可未知）、`strength='hard'|'soft'`、`included_categories` 与 evidence quote。游客说“预算”默认硬上限；若只愿尽量省，用软偏好。金额始终用整数分/Decimal，不能用二进制浮点解析金额。

“每人 800 元”保存 `amount_fen=80000, basis=per_person`，**不乘天数**；明确两人时全程团体上限为 160000 分。只说“和朋友”不推算为两人，追问共几人，并保留已识别日期/预算。人数未知时可保存条件和展示不带预算认证的参考估算，但硬预算参与的可确认规划须补齐人数/范围；不能静默丢掉预算继续称满足条件。当前 A4 是明确告知“先不按预算筛选”，该诚实降级在 B4 接通前保留。

如果同时给“全程最多 1500、每人最多 800、两人”，保留两条约束，团体上限取 150000，说明哪个更紧；不得相互覆盖。多人的“每人”首轮仅支持同一人均限额和共同行动组的平均费用口径，实际个人账单不同不在本轮处理，应向用户说明并确认口径。`guests` 是住宿入住人数，不天然等于整趟 party_size；`rooms`、房型更不能推人数。

旧单日 `budget_fen` 只读适配成该旅行的团体总上限，原单人/未知人数兼容请求不改解释。工作区里的 `total_budget_fen`、`stay_budget_fen`、`max_price` 分别是历史整趟意向、住宿合计条件、每晚筛选上限；不能选酒店后覆盖全程预算，也不能把过滤条件当实际报价。历史未明确预算作用域的值先追问，不由迁移脚本推断。

### 6.2 费用条目与未知

使用服务端 `CostItem` 快照（可嵌在路线/账本中）：稳定 `cost_id`、类别、关联 goal/leg/overnight、适用日/住宿晚、计价单位 `per_person/per_room/per_vehicle/per_party`、数量、人数/房间/车辆依据、上下限、currency=CNY、status、来源/有效期。现有 `PlaceCost` 没有计价单位，不能自动按人数乘；B4 对单日旧路径继续沿用旧口径，对多天缺单位返回未知或要求维护补充，禁止猜票价含几个人。

已知全程上限 = 已报告且已结算条目 + 尚未支付计划条目的可用费用上限。住宿在 `night_date` 计一次，次日从酒店出发不再收费；同一票种覆盖多日须有明确适用范围才复用，同一地点去两次不当然只收费一次。用费用 ID/覆盖范围去重，不能按地点名去重。房间价×间数×晚数只在明确逐晚房价、适用人数/房型及附加费范围时计算；FlyAI `nightly_from_fen` 不能当这样的上界。

返回 `known_cost_upper_fen`、`unknown_items[]`、按日/类别小计、`budget_status='within_budget'|'over'|'unknown'|'not_set'`；within_budget 仅在无未知、数量/范围明确且总上限未超时表达满足模型预算，仍标估算/用户报告依据。可显示参考起价，但不得并入可认证上限。已知费用已超上限则确定 over；已知未超但有未知则 unknown，不填 0、不显示剩余可花金额为确定值。

严格模式硬预算下：必需条目/计价数量未知返回 needs_data，不出“预算可满足”的候选。当前仅 `demo_reference` 演示模式允许未知费用不按硬预算阻塞，reference 资料本身并不放宽硬预算门；若保留此路径，必须显式告知“预算尚未核验，仅约束已知费用”，与严格结果分开，不能把整套的 cost_unknown 覆盖掉。软省钱目标也一直标“仅按已知费用比较”。

### 6.3 分配预算与途中支出

不先均分成“每天 400”硬上限。全程预算是耦合约束：外层可先为已知必选和住宿预留费用，再在有界分配/日内求解中传剩余限额，逐套汇总复核，失败可换日/替换可选实现；先解某一天不得把其余天预算耗尽。搜索未穷尽仍按 timeout 返回，不能以一天暂时超分配额宣布全程无解。按日预算仅在游客另外明确指定时作为额外约束。

沿用当前 `spent_cost_fen/spend_as_of` 的用户报告证据门，但定义为**截至该时刻全程累计**，不按天归零，不按每次重排累加同一个数。账本保存报告时点及已覆盖条目；已付酒店/票/当前活动和未付部分明确对应，不把“进行中整项费用”无条件再加一次。无法判断报告是否包含当前酒店/活动时追问，状态为 unknown。退款/取消费未知也保留，不把删除一站等同于钱已退回。日期改变不重置花费历史。

## 7. 小汴梁日期范围与证据校验

### 7.1 `PlanningField` 扩展

保留现有 date 供旧单日使用；新协议增加 `start_date/end_date/trip_days/trip_nights`，以及日字段的可选 `day_ref`。`day_ref` 是带自己原文证据的明确日期或“第 N 天”，由服务端解析到 day_id，不信任模型直接给出的 ID。`start_time/end_time/origin/destination` 无 day_ref 只在单日中继续按旧语义；多天中没有“每天/第一天/最后一天”等作用域证据时追问，不把第一天时间复制所有天。明确“每天 9 点到 21 点”可通过 `scope='every_day'` 展开；scope 自身也必须有 quote。重复检查键改为 `(field,day_ref/scope)`，不能把两天不同结束时刻误判为同字段冲突。

B4 再加入 `budget_basis/party_size` 与金额字段；金额和“每人”必须在同一预算条款关联，避免模型用 quote='800元' 丢掉原句“每人”。新增字段不由 LLM 写预算衍生总额、事实 ID、来源、route_version 或正式状态。

### 7.2 确定性解析与交叉检查

沿用 `_field_check` 的逐字段 quote 存在性、`request_evidence` 否定/引用/假设门和 dropped_fields 反馈；新增范围校验函数返回明确原因，不新增一轮模型审查。

| 原话/情况 | 规范化与校验 |
| --- | --- |
| “周末玩两天”，基准 2026-10-26 | 解析出开始 2026-10-31、天数 2，推导结束 2026-11-01；推导记录依赖原文及服务端基准日期，不能伪造一条不存在的 end_date 引文 |
| “10月31日到11月1日” | 同一连续 quote 中分别验证起止角色，按基准年份得到两天，end_date 包含当天；不能沿用当前“取最后一个日期表达式”的逻辑给两字段都取 11-01 |
| “三天两晚” | 独立保存 days=3/nights=2，缺开始日则追问；不从今天或酒店查询日期擅自填起日，也不表示已订住宿 |
| 2026-11-01 周日说“周末玩两天” | 单个“周末”的旧逻辑取当日，但两天将跨出本周末；多天解析应追问“从今天开始两天，还是下周末两天？”，不静默排周日到周一，也不偷偷挪到下周 |
| “2026-12-31 到 2027-01-01” | 显式跨年合法；缺年份的“12月31到1月1”先核对跨年，不能得到负范围或自动改年 |
| 范围、天数或天晚数矛盾 | 如“10-31 到 11-01 玩三天”、两天两晚（涉及额外住宿），保留无争议字段，单独追问冲突，不随意优先一个；若是明确自我更正，则按现有 correction 证据门处理 |
| “第二天下午去开封府” | day_ref 只定位日期；“下午”不能变造为 14:00。可存日期限定，时间仍缺；目标存在性/映射走原事实门 |

解析基准来自当前请求的服务端上海日期，并在任务中固定到解释结束；跨零点执行要检查基准是否失效，不能让同一候选两个字段用不同的 today。`trip_days/trip_nights/party_size` 用整数与现有中文 number helper，限制范围并验证原文单位。范围一致性由确定性服务统一检查：日期合法、无过去的首次出发、日数上限、每日日内时窗和目标范围；不能只验证字段分别合法。

已保存条件从共用 requirement_context 读取。改范围后，保留未变日期的明确字段；被移除日期上的目标、住宿、锁定、费用列入影响清单等待处理，不能整体搬到新日期。工作区预览可以保存明确的新意向，正式路线改变仍必须预览确认。B2 完成后不再给多天请求加 A4 的“只排第一天”提示；B4 未完成前仍如实说明按人预算限制。

## 8. 途中重规划、冻结与恢复

默认 `ReplanRequestV2.scope='current_day'`：只求解今天的剩余路线，后续日保持原目标归属、时间和过夜点。仍复核整个候选的跨夜连接及全程预算，不因只改今天绕开总约束。若当前日改动影响后续，列出冲突并建议“连后面几天一起调整”，不自动升级范围。

用户明确允许时 `scope='current_and_future'`：今天剩余及后续未冻结日参与分配；跨天必选继续全程恰好一次；固定日期/场次、已确认酒店及显式锁定未来活动保持原日/时刻/实现，除非游客在原确认流程中明确放宽。预览对每个 goal 显示 `before_day → after_day`，保留原 goal_id。用户明确问“只改明天”可用 `scope='selected_future_day'`，它是未来日预排，不能拿今天 now/锚点覆写明天起点。

“今天”由 `now().astimezone(TZ).date()` 匹配 day_id，不由页面当前 tab 决定。真正的途中重排要求 ACTIVE/PAUSED、新鲜的手动已知地点及时间；未来日编辑使用该日已确认起点/时窗。日期不在旅行内、处于两日间的夜间、或今天已结束时，追问要修改哪天/何时开始，不拼出越过零点的单日请求。`ongoing` 全程最多一个，只能属于今天的当前下一活动；保留剩余时长的依据，跨日仍未结束的活动先核对，不能挪到次日当新活动。

冻结有两层：已经 completed/skipped 的 Stop 内容逐字段不变；已经闭日或自然日已过去的 DayItinerary 保留当时计划、实际报告和来源快照，不作为 solver 变量。闭日不等于“全站完成”，旧 pending 保持“历史未核对”，不能在新版本中假装删掉或补齐。确需补录历史进度走单独的明确状态确认，记录 user_reported 和事件，不通过路线重排完成；B2 首轮可只支持查看并提示核对，不必加入任意历史编辑。

历史日里的未核对必选不视为已完成，也不自动复制到明天。它仍属于冻结历史目标集合；需要先补核其状态或明确处理该必选，才能生成声称覆盖剩余硬目标的整套新候选，否则返回 needs_clarification。若首轮未提供历史补录入口，就明确保留原路线并提示该限制，不能为排出后续天而删除历史必选。全程复核区分“保留的历史计划/实际报告”和“本次可变的未来计划”，不把历史未知认证为全程执行成功。

恢复旧 `route_version` 也只是生成新候选：冻结各天/当前前缀不恢复，已处理目标不再执行，未来目标按当前事实、日期、费用重验；确认产生新版本，不把版本号拨回去。全程最后一个日结束后，用户显式结束整趟才走原终态与留存流程。会话 24h 不活跃失效规则仍在（`memory/session.py:79`），多天不因此获得跨设备或长期恢复承诺；这需单列产品决定。

## 9. 前端最小改动

继续使用当前行程页、素材篮规划和 CityAgentChat，不新增第二个多天规划入口。先在 `explorationApi.ts/cityApi.ts` 引入 schema 判别联合与只读 `routeDays()`/`draftDays()` 适配，旧单日包成一个显示日，写操作仍保持原旧形状。日期按 Asia/Shanghai 格式化，跨日差异展示日期＋时刻。

- 行程页页头显示“10 月 31 日—11 月 1 日 · 2 天”，下方日选择条，每日显示日期、起终点及当日状态；默认今天，旅行未开始选首日，历史查看保持用户所选。`ItineraryFlow` 复用为日内时间轴，底部显示过夜点/返程；跨夜只显示住宿说明，不渲染交通线。
- 规划条件使用每日卡片：日期范围、每日日内时间窗、每日端点。可以提供“将已明确的时段用于每天”的显式按钮，操作前展示影响，不能默认勾选。按日显示缺项，酒店绑定缺项转服务候选选择/地点核对。
- 三目标每个按钮仍代表一套全程方案，内部按天折叠显示；顶部全程步行/费用，下方各日小计。第一天省略的可选若在第二天已排，不列为“未安排”；合并标签按完整全程结果判断。
- 变更预览按日展示增删、换日、换酒店及预算影响，只有一个“确认整套方案”。复用取消、旧候选失效、选择 alternative_index 等逻辑；单日仍是原一页效果，不强迫多一次日切换。
- `journey.ts`、地图与首页共用聚焦日和版本投影，地图 revision 包含 trip/version/route_version/day_id；选择明天只查看，不把明天第一站设为今天下一站。日级失效提示不遮掉已经冻结的历史，也不让仍有未来路线的旅行因首日过去整体消失。
- 当前 `CityTrip.vue` 还承担“确认当前位置”，不能简单把整个旧管理页关掉。多天至少提供该操作及按日只读进度；旧单日编辑照旧。`CityPlanner.vue` 的旧表单对多天提供主流程链接，不提交降维的 Requirements。

## 10. 分阶段实施任务

以下是可各自交付一次 Codex 会话的范围建议；新增文件名是建议，不要求机械照抄。每项都只改其范围、沿用 fixtures/Mock，不调用真实模型/服务、不导入运行库。**每项硬验收：原单天的输入→预览→确认→读取→途中重排闭环不回退，原版本/锁定/幂等/删除规则不回退。**

| 子任务 | 范围、主要文件 | 验收标准（均另含上述单天硬验收） | 风险 |
| --- | --- | --- | --- |
| B2.1 模型与兼容读取 | `engine/models.py`、`state_schema.py`、`schema.py`；新增小型版本适配模块；`engine/service.py`/`state.py` 读取分派；export scripts/对应 contracts；`test_city_architecture.py`、`test_city_state.py`、`test_city_engine_api.py` | 旧 JSON/旧请求/旧哈希不重写；新日范围、目标作用域、前缀与 day_id 验证；未知 schema 拒绝；V2 读取及导出删除覆盖；尚未接通写链路的 V2 明确 unavailable，不能误落旧单日逻辑 | 反序列化入口遗漏；默认 tag 让 union 误判 |
| B2.2 有界分天纯引擎 | 新增 `engine/multi_day.py` 及全程独立 validation；仅必要改动 `solver.py/prepare.py/tools.py/context.py`；`test_city_engine.py`、`test_city_engine_travel.py` | 两天/三天合成例；仅第二天营业的全程必选；第一天限定演出与灵活目标交换；无重复目标/场次；缺日数据不借用首日；空目标日转场；三目标全程去重；累计时限不乘天数；未穷尽不报无解；篡改跨夜/必选结果被独立复核拒绝 | 分配漏解、CPU 配额、误标全程目标标签 |
| B2.3 全程预览确认与途中状态 | `engine/service.py/storage.py/live_travel.py`、`state.py/basket.py`、`exploration.py/exploration_state.py`、`assistant/tools.py/context.py`、`agent/toolbelt.py/semantic_execution.py/service.py/conversation.py`、`weather.py` 必要投影；对应 engine API/state/exploration/assistant 测试 | 整套预览不变正式状态；确认一次 +1、幂等、并发冲突/失败回滚；跨夜连续；B2 手选已知过夜点；今日重排/连后续/未来单日三种 scope；昨日冻结、当天 ongoing、后日锁定；历史恢复新建版本；按日交通身份不覆盖；导出删除完整 | 最大事务/跨模块范围；遗漏旧工具写入口。暂未接通的多天工具必须明确拒绝，不能降为第一天 |
| B2.4 日期范围语义与追问 | `agent/models.py/planning_intent.py/understand.py/orchestration.py/requirement_context.py`，必要 `travel_services.py` 日期帮助函数；agent exporter/contract；`test_planning_understanding.py`、`test_agent_requirement_context.py`、`test_agent_backend_orchestration.py` | Mock generate 下三种背景日期说法进入同一工作区；周日周末/跨年/矛盾/否定/引用/模型伪造证据；按日时刻、目标和 null 继承；缺时间/过夜点追问；条件齐时两天预览、明确确认后才保存；移除“只排第一天”断言，按人预算限制仍保留 | 日期范围影响酒店单日 resolve_date；不能破坏 A4 单日“周末”语义 |
| B2.5 主流程按日 UI | `explorationApi.ts/cityApi.ts/journey.ts/amap.ts`、`BasketPlanning.vue/CityAgentChat.vue/ItineraryFlow.vue/CityMap.vue`、`JourneyPage.vue/CityTrip.vue/CityPlanner.vue` 及 Home/MyPage 必要卡片 | 类型/构建通过；双日预览与日切换、整套确认、跨日变更、进度/地图同版本；单日少操作；旧管理/表单不能覆盖多天。浏览器场景由 Claude 用隔离数据验收 | 表面分组但动作仍写第一天；首页/地图下一站错日 |
| B3.1 酒店绑定与过夜应用 | `travel_services.py`、`agent/requirement_context.py`、`location_resolution.py`、`basket.py` 的绑定入口及现有维护脚本（按实际导入入口）、`engine/service.py`/多天编排必要接入；`test_travel_services.py`、`test_location_resolution.py`、`test_city_engine_api.py` | 临时绑定将 selection.hotels 变成对应两日边界；候选无坐标/无绑定/重复同名不伪造；服务选择和撤销不改 route；旧服务版本/绑定修订拒绝确认；起价非总价；已指定首末端点保留；未知入住条件可见 | 数据绑定尚无真实依据；selection 过期与已选意向不能混淆 |
| B3.2 “回酒店”交互 | `agent/planning_intent.py/orchestration.py/requirement_context.py`、`TravelServiceCards.vue`、`BasketPlanning.vue/ItineraryFlow.vue`，对应 planning/travel_services 测试 | 未选问选择，已选无位置问核对，有绑定只预览当前日回酒店；末日/多个可能酒店补问；同一酒店跨两晚显示明确；路线确认才改终点/次日起点；不重复问已知条件 | 自然语言“回酒店”被当任意端点；服务确认被误当路线确认 |
| B4.1 预算模型、语义与费用口径 | 新增预算纯模块；`engine/models.py`、`agent/models.py/planning_intent.py/requirement_context.py`、必要 service 条件投影；相应 exporters/contracts；`test_planning_understanding.py`、`test_city_engine.py`、`test_travel_services.py` | 每人800、两人=1600全程，单日总预算兼容；朋友不推人数；金额单位/作用域 quote 校验；按人/房/车/团不同计价、未知单位/起价不认证；先保留旧拒绝提示直到 B4.2 接通 | 旧 PlaceCost 无单位；住宿 guests 与 party_size 混淆 |
| B4.2 全程预算求解、支出与展示 | 预算模块、多天分配/validation、`engine/service.py`、`agent/orchestration.py`、`explorationApi.ts/BasketPlanning.vue/ItineraryFlow.vue`；`test_city_engine.py`、`test_city_engine_api.py`、`test_planning_understanding.py` | 不按天重置或均分硬限额；住宿跨夜只计一次；已支出全程累计、重排不叠加；进行中/已付票明确覆盖；严格未知阻止预算认证、演示已知比较明确；按人理解→预览→确认闭环后移除限制提示；费用变化使候选失效 | 跨天预算搜索漏解；重复扣酒店/历史/ongoing；未知误当 0 |

依赖与并行边界：

- B1 经 Claude 审核后先 B2.1。B2.2 可与 B2.4 的语义开发、B2.5 的只读显示部分并行，但都基于 B2.1 固定的结构；共享模型/API 契约由 B2.1 单点维护，最终分别更新自己新增模型的导出。
- B2.3 依赖 B2.2；B2.4 端到端验收、B2.5 写操作接线依赖 B2.3。若同时碰 `service.py/exploration.py/assistant/context.py`，按上述次序串行，不能在同一文件并行改。
- B2 全部验收后，B3.1 与 B4.1 可以独立 worktree 并行，分别只动酒店绑定、预算纯模型/语义；共同的 `engine/models.py` 等契约先定好扩展点，否则串行。B3.2 在 B3.1 后，B4.2 在 B4.1 后。
- B4.2 若要验住宿计费，应在 B3.1 的过夜绑定合并后做集成；纯预算计算测试可先用手动 Overnight。B3.2 与 B4.2 的 Agent/预览 UI 文件重叠，最终接线串行。
- 每个子任务按用户通用规则跑 `.venv/bin/python -m pytest backend/tests -q -p no:cacheprovider`；有前端变更再跑 `cd frontend && npx vue-tsc --noEmit && npx vite build`；契约变更用对应 exporter 生成并 `--check`。浏览器 e2e、真实模型、真实服务及真实酒店/场次数据由 Claude/队员后续独立验收，Mock 通过不替代它们。

## 11. 风险与待决定事项

### 11.1 实施时必须处理的风险

1. **分天局部可行不等于全程可行。** 目标重复、必选漏分、酒店断点、预算重复都要有独立全程 validator；不能只运行两次旧 solver 就返回成功。
2. **未来资料与报价有效期。** 营业/演出需每日真实适用事实；第一天覆盖不能外推第二天。未来交通报价是参考快照，15 分钟候选有效不等于未来交通保证。读历史不重新认证旧实际记录。
3. **版本投影遗漏。** 旧 V2 助手工具、管理表单、地图和导出仍在，任一读取单日的入口都可能丢后续天；新 schema 分派必须覆盖盘点清单，未支持入口明确拒绝有损写入。
4. **时间预算增殖。** 分天×分配×目标×补备选会放大 CPU 和网络调用；外层总限额及测试先于提升天数。基准至少覆盖 1/2/3 天、5/15/30 目标与不同场次展开，不承诺未测的响应时间。
5. **住宿可达与可入住混淆。** 酒店坐标/路程可用不证明有房或已预订；参考路线和住宿条件缺口同时展示。B3 真实候选无绑定时如实降级，不自动建正式地点。
6. **留存限制。** 当前 guest 24h 不活跃、结束旅行后 72h 等规则不随跨天暗改；真实多天使用的会话连续性要单独决定，不能宣传“第二天一定恢复”。

### 11.2 请 Claude/用户/队员在相应实施前决定

| 决定 | 本文推荐默认值 | 谁在何时决定 |
| --- | --- | --- |
| 首轮天数/规模与求解限额 | 连续 1–3 天、30 目标、日内24站/60候选；全程 CPU 默认1秒/上限3秒，基准不足则 timeout 后再调 | Claude 审 B1/B2.2 时确认；需要扩大再明确修改上限 |
| 周日“周末两天”、不带年份跨年 | 追问，保留单日原语义；不猜下一周或下一年 | 负责人审 B2.4 的固定产品规则 |
| 默认途中修改范围 | 仅今天剩余；连后续天必须明确选择 | 负责人审 B2.3 |
| 历史 pending 的补录 | 不自动完成；首轮只读提示，闭日前必须显式处理未完项；任意历史编辑另排任务 | 负责人审 B2.3/前端文案 |
| 酒店坐标/事实绑定责任及首批覆盖 | 队员提供带证据绑定；先一家酒店覆盖全程，不加自动酒店地理编码服务 | 队员在 B3.1 真实验收前；无数据时保留 needs_data |
| 未核实入住条件是否阻止参考路线确认 | 可确认有明确缺项的参考路线；不得声称住宿已落实；严格入住保证不提供 | 用户/负责人审 B3，若要求更严格则改为补齐前阻止确认 |
| “每人预算”是否覆盖市外往返、购物、个人不同花费 | 默认只覆盖本系统规划的市内游玩/餐饮/交通/住宿，预览明确范围；不同个人账单不处理 | 用户补预算范围；负责人审 B4 的追问与展示 |
| 旧费用记录的计价单位、已付款覆盖关系 | 队员补来源/单位；未知即未知，不做批量推断迁移 | 队员在 B4.2 数据验收前 |
| 多天的匿名会话过期体验 | 沿用 M7，先提示保留限制；是否延长会话/引入恢复机制另立授权任务 | 用户/负责人在真实两天游客验收前 |

以上待决定不影响 B1 文档交付；实施者采用经 Claude 审过的默认值，不把设计建议、合成验证或参考资料写成真实多天行程已验收。

## 12. Claude 审核结论（2026-10-26）

设计通过，按第 10 节分阶段实施。审核时确定：

1. **采用 11.2 的推荐默认值**：首轮连续 1–3 天、全程 30 目标、全程求解默认 1 秒上限 3 秒；周日说"周末两天"追问；途中修改默认只改今天剩余；历史未完成项首轮只读提示；酒店先一家覆盖全程；未核实入住条件的参考路线可确认但不得声称住宿已落实。
2. **首轮收窄**：B2 首轮只要求出发前的"多天预览 → 确认 → 查看"闭环和"今天剩余"的途中重排；`current_and_future`、`selected_future_day` 两种重排范围和历史补录放到 B2 之后，未接通时明确拒绝（不降为第一天）。
3. **验收方式**：每个子任务除 Codex 自测外，Claude 用不提供给 Codex 的多种说法做真实模型验收，并重跑单天主流程稳定性（演示句 10 次）；单天任一回退即不合并。
4. **需要用户/队员决定的事项**（预算覆盖范围、匿名会话 24 小时过期对多天的影响、酒店绑定数据）在 B3/B4 开工前由 Claude 汇总提问，不阻塞 B2。

## 13. B2.3 实现边界（2026-10-27，待 Claude 验收）

后端已接通显式 `city-engine/2` 的出发前整套预览、一次确认、完整读取，以及 `current_day` 的今天剩余重排。预览只保存整套候选及幂等回执，不改正式路线、草稿或版本；确认在 `BEGIN IMMEDIATE` 中复核聚合版本、路线版本、进度/锚点、素材篮、规划工作区、事实指纹、TTL 和交通报价，再独立校验、写整套路线、更新草稿及事件/回执、消费候选。任何失败由原 SQLite 连接上下文回滚。确认一次全程路线版本加一，相同请求回放原回执，并发不同请求只有一个可提交。

今天由服务端上海日期和当日日内时窗确定，不依赖页面选日。历史必选仍 pending 或历史 ongoing 尚未报告完成/跳过时，保留原路线并提示历史待核对；历史补录入口未开放。今天重排保留已处理前缀和当前 ongoing，后续天逐字段锁定，原始每日需求及过夜边界不变。已报告处理的昨日 ongoing 仅在新候选中将其 `DayItinerary.status` 标为 `closed`，使它成为封存的历史预约快照；其中原计划、ongoing 内容及来源不变，原正式版本和历史 `DayDraft` 不改写，也不把历史 pending 自动改为完成。当前态 ongoing 数量只统计未封存日。普通历史日仍保留原快照；读取响应另外给出按日期推导的 frozen/locked/active 状态。

`restore_route_version` 仅在引擎 API 支持恢复今天的未处理目标，按当前事实和锚点重新求解、生成整套新候选，确认产生新版本。不会恢复历史进度、回拨版本或挪动后续日目标；日期范围不符、目标现已归属其他锁定日、未处理历史必选等仍拒绝。旧助手的自然语言恢复工具尚未接通。

B2 只接受 `user_place` 已有地点；过夜地点证据由服务端事实计算，住宿条件保持 unknown，客户端不能声明已核验住宿或提供酒店绑定。交通派生身份含日期、时间窗和报价时点，按日筛选使用；多天查询共享同一 provider 请求限额和截止时间，快照恢复遍历所有天。多天对象嵌入原路线/草稿/候选 JSON，导出与删除沿用原所有权及外键级联，新增测试覆盖完整三天和全部候选。

| 写入或消费入口 | B2.3 状态 |
| --- | --- |
| 引擎 `preview / replan-preview / confirm / current / versions` | 已接通整套路线；重排只支持今天剩余 |
| 状态 `create`、显式 V2 `set_anchor / mark_completed / skip_stop / close_day / transition`、状态候选与确认 | 已接通；历史冻结、未来锁定，重要变更仍需确认，闭日不结束整趟 |
| 状态整体 `save_draft`、旧单天请求写多天草稿 | unavailable；不能有损写回第一天 |
| 素材篮 `put/remove` 及按日 arranged 投影 | 沿用已接通的旅行工作区；不改正式路线；变动使旧路线候选失效 |
| `exploration.preview / answer / cancel`、V2 `exploration_state.save` | unavailable；日期范围语义与工作区接线留给 B2.4 |
| 小汴梁 V3 D4 延误、多待、休息、跳站 | 已接通今天切片→整套候选→统一确认；其他天保留 |
| V3 普通规划、偏好、位置/完成/旧跳过、取消旅行、意愿暂存 `_defer`、自然语言恢复 | unavailable；需用已接通的显式 V2 状态或引擎 API |
| 旧助手 `preview / propose_state` 与旧状态确认适配器 | unavailable；路线确认和候选丢弃已接通 |
| `assistant.context`、`semantic_execution`、`conversation` | 明确今日切片及日 ID，保留完整多天快照、范围及各日冻结/锁定；无今日剩余时不指向明天首站 |
| 天气查询 / 天气重排 | 查询只说明今天切片并带日期；多天天气重排 unavailable，调用 Provider 前拒绝 |
| 旅行服务工作区 | 仍仅为候选意向；不建立 B3 酒店绑定、不改正式路线 |

仍拒绝 `current_and_future`、`selected_future_day`、历史补录、多天整体草稿覆盖、酒店绑定及全程费用报告。D4 请求中显式日期、数字日序和数值天数范围会被确定性核对；现有 `StateIntent` 没有范围字段，且本任务禁止改提示词/字段说明，因此“后几天一起”等没有日期或数值范围的说法尚不能可靠结构化，需 B2.4 补齐，不能把当前 Mock 验收说成这类真实语义已通过。

软件验证入口：[整套事务/API](../backend/tests/test_multi_day_api.py)、[日级状态](../backend/tests/test_multi_day_state.py)、[助手与 D4](../backend/tests/test_multi_day_assistant.py)、[按日交通](../backend/tests/test_multi_day_travel.py)。这些测试使用隔离库、合成事实及替身，不代表真实模型、交通、住宿、现场或游客验收；前端属于 B2.5，本轮未修改。

## 14. B2.5 前端实现边界（2026-10-28，待 Claude 验收）

前端按 schema 区分旧单日与多天草稿/路线，只读日投影不改原 JSON，写操作保留服务端契约。行程页提供日期范围、默认今日及按日只读切换，复用日内时间轴并单列过夜说明；跨夜不生成交通段。素材篮与 Agent 多日候选复用整套方案展示，按日折叠、汇总步行/已知费用、显示各日变更，一次确认同一候选的完整 alternative。助手本轮返回的工作流候选直接交给预览组件，避免工作区未持久化 result 时丢失候选。地图 revision 包含旅行、状态版本、路线版本与日 ID；选未来日不标为今天下一站。旧单日表单禁止覆盖多天，管理页保留今日位置确认及按日只读进度。

进行中的多天每日条件表只读，调整今天剩余交给已接通的 Agent 流程；后续天调整、历史补录、酒店绑定、全程预算没有新增可点的入口，错误和 unavailable 回复保留后端说明。未改后端业务、模型契约或提示词，未调用真实模型、地图或其他外部服务。

已知后端阻塞：`exploration.preview_plan` 的 V2 分支生成候选后递增工作区版本，未同步候选的 `planning_version`，通过该入口生成的候选确认返回 `409 PLANNING_CHANGED`。[隔离 API 测试](../backend/tests/test_multi_day_browser_fixture.py) 保留 strict xfail 复现；直接 engine 预览、整套确认与双日读取正常。浏览器测试服务器只准备已有引擎候选快照，不在生产前端绕过版本门。

验证入口：[离线 TS 投影](../backend/tests/test_multi_day_frontend.py)、[五个多日浏览器场景](../frontend/tests/multi-day.spec.ts)、[隔离浏览器配置](../frontend/playwright.multi-day.config.ts)。全量后端为 2530 passed、9 skipped、1 xfailed；类型检查、Vite runner 与原 `npx vite build` 均通过（原 loader 首次因外链 node_modules 临时目录 `EPERM` 失败，改用本 worktree 内临时依赖入口后通过，原链接已恢复）。现有五组及新增多日浏览器服务均因本地监听端口 `EPERM` 未启动，`docs/screenshots/multi-day-*.png` 尚未生成。这些软件检查不代表真实开封数据、住宿、导航或现场验收。

## 15. B2.6 每日条件与住宿日期（2026-10-28，待 Claude 验收）

`every_day` 的证据允许来自字段 quote 或包含 quote 的同一句，句末标点及换行断开，逗号并列保持同句；数量/范围天数加“都”和“每一天/每日/天天/各天”按语法核对。漏标作用域仅在已知多天、无 day_ref、同字段本轮唯一值且有同句证据时恢复，记录 `recovered_fields.source=sentence_scope`；缺证据继续追问。后续明确每天的答复可替换未定日的同字段，不影响其他条件。

已有相邻日终点和起点相同即可形成 `user_place` 过夜边界；酒店查询位置无法映射到该边界时继续追问，不能替换端点。酒店查询在本轮或已保存的有效多天范围内补缺失入住日/晚数，分别采用第一天和天数减一；游客明确日期、晚数或退房日优先，单天不补。回复回显采用的日期和晚数，`service_date_defaults` 记录补齐项，已补上的日期项保留丢弃审计但不重复告知游客“未采用”。本轮范围由确定性规划预备结果共享给服务查询，再走原预览/确认入口；酒店仍仅是查询条件，没有候选绑定、可入住或预订保证。

验证入口：[B2.6 正反例及走样输出](../backend/tests/test_b26_multi_day_conditions.py)、[日期范围回归](../backend/tests/test_b24_date_range.py)、[讲稿录制回放](../backend/tests/test_live_model_replay.py)、[逐句提示词与长度检查](../backend/tests/test_agent_prompt_schema.py)。理解提示词含 schema 的字符数由 7993 调整为 7999，`prompt_rules_main.json` 保持原样；均使用隔离库和模型/服务替身，真实模型、外部服务及现场未验收。

## 16. B3 酒店候选与相邻日衔接（2026-10-29，本地集成验收）

`selection.hotels` 保存选择时的 `stay` 日期和 `selection_version`。服务候选确认与撤销只改变意向，不修改正式草稿、路线或历史进度。`hotel_binding.project` 将同一酒店覆盖的相邻日连接为 `selected_hotel` 过夜边界；小汴梁统一条件投影、素材篮预览、引擎直接预览共用此投影。整套路线确认后才写入正式日端点与绑定快照。首日起点、末日终点不变，额外住宿不延长游玩日期；日期仅证明所需晚数，实际入住/退房时刻保持未知，酒店起价不参与总价认证。

地点使用已有 `city_object_bindings(kind='flyai_hotel', object_id='flyai:<shId>')`；新增 `city_hotel_binding_metadata` 保存显式地点 ID、修订号、来源、核对时刻及有效区间。维护者通过 `scripts/import_hotel_bindings.py bindings.json --database /absolute/path/to/db.sqlite3` 在内存副本预检；加 `--apply` 才原子写入。输入为 JSON 数组，每项字段为 `candidate_id/place_id/revision/source/checked_at/valid_from/valid_until`，时间须带时区。仅能引用库内已有地点，修订须递增；同名、地址及 provider ID 本身不构成事实映射。不提供游客写入映射接口。

候选记录服务工作区版本和地点绑定指纹，计算结束及最终确认各复核一次。换酒店、重新查询、撤销或修订地点绑定后，旧预览拒绝确认。读取旧路线发现当前意向不同会标记过期并保留原快照。搜索结果过期限制新选择，已确认的酒店意向不会因三分钟搜索缓存到期而消失。未映射地点返回 `HOTEL_LOCATION_PENDING`，住宿区间不足返回 `HOTEL_STAY_RANGE_MISMATCH`，旧意向缺日期快照需重新确认；未知交通与事实有效性仍由原引擎检查。新建草稿禁止自行携带酒店绑定元数据。

本轮软件集成使用隔离 SQLite、合成地点/交通和 Mock 酒店响应；真实模型及业务 Provider 请求为零。最终后端全量 **2975 passed、9 skipped**（安装目录以本地依赖链接复用，原先跳过的 5 项前端投影测试本轮运行通过）；前端类型检查、构建、四组契约导出检查通过，维护 CLI 的 dry-run、显式导入及无效导入不改旧映射检查通过。Playwright 酒店候选 6 项、多日行程 6 项通过，包含从现有酒店卡片选择到实际引擎规划、确认和双日行程页读取的新场景。后端验证入口：[酒店衔接 API/Agent/撤销与版本回归](../backend/tests/test_hotel_overnight_binding.py)、[既有酒店响应](../backend/tests/test_travel_services.py)、[多日状态](../backend/tests/test_multi_day_state.py)；浏览器入口：[多日集成](../frontend/tests/multi-day.spec.ts)。本地端口监听先受沙箱限制，允许本地监听后复跑通过。前端生产布局与组件未改。

真实酒店地点映射尚未导入运行库，当前页面所在的运行服务未更新。本轮不包含途中更换住宿并联合重排后续日（现有仅今天剩余重排不允许改跨日边界）、多家酒店或住宿费用联合预算，不能据此声称真实预订或入住已经落实。


## 17. B4 全程费用与预算（2026-10-29，本地集成验收）

本节记录 `codex/b4-trip-budget` 的实际实现，覆盖住宿计费、全程预算和打车费比较；第 16 节的酒店地点绑定保持不变，后续天调整未开放。费用范围是本系统规划的市内活动、餐饮、交通、住宿，未包含市外往返、购物和分人账单。

### 输入与核算

- `MultiDayConstraints` / `MultiDayRequirements.pricing` 为可选的统一费用条件：`party_size`、`vehicle_count`、`driving_basis`、`total_budget_fen`、`per_person_budget_fen`、`per_person_daily`、`dining_per_person_day_fen`、`hard_limit`。金额均为整数分；全程与人均上限同时设置时采用较紧上限。仅明确“每天”才乘天数，人均条件缺人数不猜一人。
- `PlaceCost.unit`、`Journey.cost_unit` 及边界收费单位为 `per_person` / `per_party`。旧非零费用没有单位时列为未知；0 元仍是确定的免费，不批量推断迁移。未启用 `pricing` 的旧请求保留原算法口径。
- 酒店 `/services/preview` 可带 `room_id`、`rate_id`、`rooms` 选择服务端报价，或 `manual_price={amount_fen,unit,rooms}`。两者互斥，只用于酒店。接口报价须有 CNY 币种、匹配的入住区间及明确单位，并核对已提供的晚数/人数条件；未明确单位时只能核对后手填。房型容纳人数不足或未知不能据此确认完整预算。
- 住宿单位为 `per_room_night`（每间每晚）、`per_room_stay`（每间整段入住）、`party_stay`（全组整段入住）。整段价格只计一次，不再乘晚数；额外入住日期属于所选整段报价时计入并注明。手填标为 `reported`，搜索起价保持参考，不转为房型报价。当前一个酒店报价覆盖整趟住宿，与 B3 的一家酒店范围一致。
- 打车使用 `vehicle_cost_fen × vehicle_count`，不再乘人数；明确自驾时油费/停车费未知，不拿出租车估价替代。过路费是否包含在打车估价中不明确时单列待核对，避免直接重复加。按人每天餐费预留替代路线内餐饮估价，不再叠加同一餐费。

### 求解、确认及已支付

`costing.py` 是统一计费入口。多日 `budget` 包含逐项金额/单位/数量/来源/依据、全程已计入额、人均、按分类和按日汇总、上限及未计入项；住宿整段金额归属入住日，按日金额不表示每晚均价。未知不填 0，也不因此声称预算足够；报价、估计、手填依据分别标明。

省钱求解纳入按人/按车换算后的金额，明确费用未知的路线不作为 0 元优胜。普通省钱先保留目标收益，再比较费用；硬预算搜索另尝试省钱候选，必选目标不删，可省略可选目标。最终按整趟费用复核，不将总额平均分给每天；当前仍是有界分天/日内搜索，未找到满足预算的候选不证明全局无解或最优。住宿和统一餐费属于固定支出，参与全程核对。

报价选择通过原服务工作区预览/确认保存；路线候选同时受原酒店选择版本保护。确认时重新核算费用和报价有效期，拒绝过期或金额快照不一致的候选；已确认路线保留原明细，后续读取发现报价过期/依据变化会提示刷新，不静默覆盖历史。

途中 `pricing.spending={amount_fen,as_of,covered_cost_ids}` 表示累计已支付金额及其**完整覆盖**的费用项，可包含预付住宿。新报告时刻须在 15 分钟内，费用项必须来自已确认明细，累计更新不能遗失此前已覆盖项；交通仅在该段完成后报告。报告金额替代勾选条目，不与原估价重复相加，已完成活动与已消费交通保留服务端费用快照。暂不拆分单项部分支付；原无覆盖范围的 `spent_cost_fen` 输入明确提示使用完整报告。预览和确认仍只重排今天剩余，其他日期逐字段锁定。行程页提供勾选、报告、预览与确认入口。

### 页面及验证

原候选卡增加报价/手填金额、计价单位和房间数；原多日条件表增加全程费用条件；预览和行程页共用 `BudgetBreakdown.vue`，已付项只显示人可读名称。保留既有布局及整套确认，不增加页面、外部服务或自动预订。

2026-10-29 验证命令与结果：

- `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q --tb=short`：**3003 passed / 9 skipped**。
- `npm run build --prefix frontend`：类型检查、Vite 构建通过，保留已有产物体积提示。
- 四个 `scripts/export_*_contracts.py --check`：全部匹配；引擎 v1/v2 JSON Schema 由 exporter 生成。
- `npm run test:e2e --prefix frontend -- --config=playwright.multi-day.config.ts`：**7 passed**，含手填住宿价格→全程预览→确认→刷新→途中报告已付住宿→重排→确认→读取不重复计费。
- `npm run test:e2e --prefix frontend -- --config=playwright.services.config.ts`：原旅行服务 **6 passed**。

新增软件测试入口是 `backend/tests/test_trip_budget.py`，覆盖单位换算、0/未知区分、整段报价、报价日期/币种/单位/人数、硬预算、打车成本比较、必选保留、非平均每日预算、过期报价、被篡改明细、累计支出、重复重排和导出。全部使用临时库/合成业务数据，无新增真实模型、FlyAI 或高德请求；真实房型报价覆盖、单位资料及真实全程费用准确性仍需业务数据验收。8033 未重启或切换，未合并或推送。
