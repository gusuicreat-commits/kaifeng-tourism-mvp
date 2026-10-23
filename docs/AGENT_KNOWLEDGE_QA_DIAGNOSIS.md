# 小汴梁旅游知识问答异常诊断

诊断日期：2026-10-23，Asia/Singapore（UTC+08:00）。本轮仅诊断，未实施下述修复。

## 结论与证据边界

这次不是“数据库没有四味菜商家，所以无法回答菜品知识”这么简单。截图首轮确实调用了 Kimi，HTTP 200 后进入理解失败兜底，**没有执行菜品或商家查询工具**。后续“系统没把资料调出来”缺少对应工具证据。与此同时，当前产品把一般知识和实时业务事实混在同一组限制与查询路径里，这一设计缺陷已通过源码和隔离对照确认。

必须分开三个结论：

1. **截图首轮的直接故障已定位到理解失败兜底这一层。** 真实回执记录为 `needs_clarification`，`understand.fallback=true`、`provider_failure=null`，Kimi HTTP 200、一次请求、约 2.97 秒、工具 0 次。结合当前处理器，可以将失败缩小到已返回对象的意图结构、原文引用或字段校验；不能确定具体是哪一项。
2. **知识问答被过度限制、默认落入业务查询，是当前代码的确定性缺陷。** 这解释了为什么即使避开首轮校验失败，类似知识问题仍会得到“资料待添加”“先生成路线”甚至剩余行程清单。但不能把这条分支说成截图首轮已经实际进入的分支。
3. **后续故障解释没有依据。** 前一轮没有餐饮工具调用或工具查询失败记录，模型上下文也没有该轮结构化诊断状态。系统接受了模型自行给出的内部原因。本轮确认的是“解释未经证据支持”，不是倒推一次从未发生过的商家查询。

没有保存历史完整 Prompt、模型原始 JSON 或具体校验异常，不能还原截图首轮的模型分类、字段和异常子类型。未追加真实付费调用，不能声称已经稳定重现 Kimi 自然产生同一错误输出。

## 1. 实际调用链

### 1.1 截图页面使用哪条实现

仓库确有旧单景区、V2 城市聊天与 V3 Agent 三条实现；截图路径使用 V3，不是看到“V3 文件存在”便作推断。

| 环节 | 代码位置 | 实际行为 |
|---|---|---|
| 详情页快捷提问 | `frontend/src/components/DetailAsk.vue:9-10` | 按钮本身提供“这道菜有什么特色？”，携带 `food/siweicai` |
| 问题与对象传递 | `frontend/src/assistantHandoff.ts:12-14` | 生成 `context=city`、`focus_kind`、`focus_id`，暂存本次问题 |
| 选择聊天组件 | `frontend/src/pages/Assistant.vue:13-18,28-31` | 该 URL 的 `cityMode=true`，挂载 `CityAgentChat`；非城市模式才挂载 `GuidedPlanner` |
| 前端发送 | `frontend/src/components/CityAgentChat.vue:26-54` | 先读 context，再 start task，循环 step；无规划输入时直接展示 `result.reply` |
| 请求地址 | `frontend/src/cityAgentApi.ts:30-37` | `POST /api/city/v1/trips/{trip}/agent/tasks`，随后 `/tasks/{task}/step`；确认另走 `/assistant/confirm` |
| FastAPI | `backend/app/city/agent/api.py:26-38`；`backend/app/main.py:61-62` | V3 task API 与 V2 API 都注册，但本页提交走 V3 |
| 授权、快照 | `backend/app/city/agent/service.py:106-129` | 版本、路线版本和所有权检查；脱敏后 `context.load(...,'modify',...,page_focus)` |
| 上下文装配 | `backend/app/city/assistant/context.py:76-120,170-220` | 从结构化状态、事实目录、素材篮和同旅程消息构建快照 |
| 理解与 Prompt | `backend/app/city/agent/understand.py:94-177` | 规则建立底稿；除明确状态读/控制外调用模型；随后执行 Schema、引用和字段校验 |
| 当前模型入口 | `backend/app/city/agent/provider.py:42-96` | Kimi JSON object 输出；一次 HTTP，不自动重试；实际历史回执为 `kimi-k2.6` / `api.moonshot.cn` |
| 调度 | `backend/app/model_schedule.py:20-31,44-95,142-160` | 进程内限流、单并发、默认 3 RPM/25 秒间隔；产品任务 45 秒；实际配置是否覆盖默认值不从源码默认值推定 |
| 分流 | `backend/app/city/agent/understand.py:193-200` | `conversation` / `clarify` / `planning` / `direct` / `react` / `workflow` |
| 执行与回复 | `backend/app/city/agent/service.py:383-465` | 不同模式采用不同回复组件，见下表 |
| 显示和留存 | `frontend/src/components/CityAgentChat.vue:54,90,93`；`backend/app/city/agent/service.py:156-181` | 主回复直接显示并留存；附加 `answers` 单独显示；没有统一的“工具后自然语言回答”阶段 |

### 1.2 不同任务的回答到底是谁生成

| 模式 | 回答来源 | 能否不调用工具直接回答 |
|---|---|---|
| `conversation` | 理解模型同一份 JSON 中的 `conversation.reply`，服务端直接返回 | 可以，但当前 Prompt 限制了知识使用，且内容只做长度/类型校验 |
| `clarify` | 服务端拼接“请先明确：”与 unresolved/questions | 不会继续生成知识解释 |
| `direct` | V3 调用 V2 的内部 `_query` 函数，重新按规则分析原文 | 不再调用 LLM；这是共享函数，不代表走了旧 V2 HTTP API |
| `react` | 模型只选择白名单动作；工具返回后由 `_action` 等确定性分支生成终止文案 | `finish` 能不调用新工具，但只能输出固定结束结果，不能自由生成知识答案 |
| `planning` / `workflow` | 字段校验、现有规划或预览服务 | 不提供独立知识回答；正式路线仍需确认 |

工具选择器只支持 `search_dining/query_shows/verify_travel/preview_plan/finish/clarify`（`models.py:114-117`）。`_eligible` 再按前置条件收窄；`service.py:306-316` 的 Prompt 要求从合法动作集合中选择。这里没有公开网页知识检索工具，也没有自由知识回答工具。高德地图服务的存在不能证明本条会话查询过公开知识来源。

### 1.3 运行版本证据

只读检查 5177 当前实际提供的 Vite 模块：`Assistant.vue` 包含 `CityAgentChat`，聊天组件调用 `cityAgentApi.start/step`，API 模块包含 `/agent/tasks` 而没有 `/assistant/chat`。8017 `/api/health` 返回 200；进程为本仓库的 uvicorn `--reload` 与 Vite。

截图旅程的五轮回执均属于 `agent_chat`，没有对应 `city_chat`。现有启动日志有 V3 POST，没有 V2 chat POST。这排除了“截图实际提交到旧 V2 HTTP 聊天”的解释。历史完整前端构建和后端源码没有逐任务哈希，因此**不能证明截图当时的所有代码字节与当前文件完全一致**。

真实调用证据来自已有回执，不是本轮重发截图对话。详见 [脱敏运行证据](reports/knowledge-qa-runtime-20261023.json)。该文件只保留场景标签、时刻、状态、计数、白名单 provider 元数据和布尔比对，不包含原始敏感对话、用户 ID、位置、Cookie、Key 或完整请求。

## 2. 根因排序与排除表

### 2.1 截图对应五轮真实记录

以下时间已由 UTC 转为 UTC+08:00；`tool_count=0` 不是“完全未读数据库”，快照装配本来就读取数据库。这里判断的是没有执行餐饮等 Agent 工具。

| 轮次标签 | 时间 | Kimi 返回 | 最终状态 | 工具 / 决策 | 关键证据 |
|---|---|---|---|---|---|
| feature | 08:17:37 | HTTP 200，一次，约 2.94 秒模型耗时 | `needs_clarification` | 0 / 0 | 唯一 trace 为 `understand`，`fallback=true`，`provider_failure=null`，回复匹配理解失败模板 |
| punctuation | 08:18:12 | HTTP 200，一次 | `answered` | 0 / 0 | `understand` 来源 `live_model`，非 fallback |
| age | 08:18:23 | HTTP 200，一次 | `answered` | 0 / 0 | 同上，证实该会话并非所有聊天都被阻断 |
| why_wrong | 08:19:10 | HTTP 200，一次 | `answered` | 0 / 0 | 已有回复确实包含“没把四味菜的资料调出来”和“只能根据已有的服务信息” |
| service_info | 08:20:01 | HTTP 200，一次 | `answered` | 0 / 0 | 继续使用非 fallback 模型回复 |

回执保存 trace、计数与 usage，但不保存 `mode`、原始模型对象或校验异常详情（`service.py:162-175`）。本轮没有把缺失字段臆造出来。

### 2.2 排除法

| 假设 | 验证方法与实际证据 | 判断 |
|---|---|---|
| Prompt 过度限制 | `understand.py:122-123` 禁止补充 context 外的景点介绍等事实、禁止用模型记忆，并要求交给 query；所有 25 条隔离轨迹捕获的是同一份实际 Prompt 哈希 | **CONFIRMED：当前规则过宽。** 对截图首轮具体 JSON 的作用无法单独量化 |
| 首轮已被路由到商家工具 | 真实 trace 只有 `understand`，fallback、0 工具、0 ReAct 决策 | **EXCLUDED：不是截图首轮实际执行路径。** 原始模型试图输出什么仍为 INCONCLUSIVE |
| 普通知识落入错误业务兜底 | 固定 query 输出，经真实 task API，知识问题变成缺路线提示或活动清单 | **CONFIRMED：当前代码缺陷。** 不能替代首轮原始分类证据 |
| 四味菜业务资料缺失直接造成首轮模板 | 当前目录无四味菜 food record、无本地 ID binding；但首轮在理解层已终止，没有走该工具或 `_query` 回复 | **EXCLUDED：作为首轮直接原因。** 当前缺数据本身 CONFIRMED，历史目录完整快照未留存 |
| 上一轮行程强制污染后续知识问题 | 固定单轮/多轮输入，第二轮合法 query 仍走 direct，没有被 `_merge` 强制改回 modify；当前模型 payload 不注入 summary | **EXCLUDED：强制合并及当前摘要注入解释。** Kimi 自然语义是否受历史影响 INCONCLUSIVE |
| 缺少工具状态使错误自我解释不受约束 | payload 仅有历史文本等，没有 prior trace/error；无证据的解释可被 conversation 原样返回；历史对应解释真实存在 | **CONFIRMED：上下文/校验缺口与无依据解释。** 不能据此确定模型内部生成原因 |
| RPM、45 秒预算或 provider 超时导致截图首轮 | 首轮 HTTP 200、约 2.97 秒完成、无 provider_failure，远未达 45 秒；无餐饮工具，未耗尽工具预算 | **EXCLUDED：该次首轮的这些原因。** 不代表其他请求没有遇到限流 |
| JSON/Schema/原文证据校验失败 | 真实 fallback 模式与当前 `parse` 的 ValidationError/ValueError 处理一致；三个不同固定非法对象都复现完全相同模板 | **CONFIRMED：失败层。INCONCLUSIVE：具体异常子类型和模型原始字段** |
| 模型自行解释了并不存在证据的查询故障 | feature 无查询工具；why_wrong 却声称资料未调出；没有保留对应查询失败记录，且接口不检查此类断言 | **CONFIRMED：解释未经支持**，不是工具提供的根因 |
| 页面使用旧聊天实现 | V3 回执 scope、当前服务模块和日志均指向 V3 | **EXCLUDED：旧 HTTP 聊天路径。INCONCLUSIVE：历史逐字源码/Prompt 版本** |
| Kimi 不懂四味菜或文化知识 | 当前 Prompt 禁止用模型记忆；没有做不带该限制的真实模型对照 | **INCONCLUSIVE**，不能把应用约束造成的失败归咎于模型知识不足 |

## 3. 可重复隔离复现

测试新增在 `diagnostics/knowledge_qa/test_knowledge_qa_diagnosis.py.txt`。之所以保留 `.txt` 后缀，是因为当前 uvicorn 监视整个仓库的 `.py`；直接新增后端测试文件可能触发生产开发进程自动重载，违反本轮“不重启 8017”。运行器把自身复制到 `/tmp` 的临时测试模块，使用既有 pytest fixture、临时 SQLite 与固定模型返回；结束自动删除临时模块及测试库。

```sh
.venv/bin/python diagnostics/knowledge_qa/test_knowledge_qa_diagnosis.py.txt
```

结果：**19 项诊断测试通过，产生 25 条受控任务轨迹，覆盖全部 10 个场景与补充分支。** 测试断言的是当前缺陷及控制流，不是知识问答修复验收。外部 HTTP transport 被禁止，模型为 Mock，没有真实付费请求。详见 [隔离轨迹](reports/knowledge-qa-isolated-20261023.json)。

| 场景 | 控制变量 | 实际路由、工具和结果 | 对要求的判断 |
|---|---|---|---|
| 1 菜品特色 | 固定模型输出 query；比较完整名称与详情页“这道菜” | 全名 → direct/no-route/needs_data；详情指代 → direct/待添加/needs_data；工具 0 | 确认业务兜底阻断知识回答；不证明 Kimi 必然输出 query |
| 1 对照 | 同样输入，改为合法 conversation 固定知识占位文本 | conversation/answered，文本原样返回，工具 0 | 说明底层可输出文本；阻碍不是完全没有聊天通道。占位文本不验证四味菜事实 |
| 2 知识追问 | “四味菜为什么叫四味菜？”固定 query，有 food focus | direct/needs_data，却答“先生成候选路线” | 有 focus 也不保证触发详情函数，词汇门槛与默认 topic 存在缺口 |
| 3 附近商家 | 相同搜索意图；无位置 vs 合成确认位置 | 无位置先 `FRESH_POSITION_REQUIRED`；有位置搜索一次，`no_candidates/FOOD_NOT_VERIFIED`，最终 needs_data | 商家真实性没有因空结果被放宽，但文案仍面向“可确认安排”而非直接查询问题 |
| 4 知识+找店 | 同时注入 conversation 和 search action | Schema 互斥失败 → clarify，工具 0；仅保留搜索 → react，空结果后固定结束，知识部分丢失 | 当前不能在一份合法结果内完成两部分，也没有工具后知识补答 |
| 5 行程后追问 | 首轮提出加入四味菜；固定未执行意图；第二轮“它有什么特色？”query | 同旅程历史包含四味菜，focus.name 正确；第二轮仍 direct，返回缺路线提示，没有重复执行加入操作 | 指代信息可到达模型；知识回答仍缺失。首轮未被宣称实际加入成功 |
| 6 空商家后问原料 | 合成已确认行程+空商家返回，再问通常做法 | 第二轮 direct/answered，**返回尚未处理的行程活动**，不是原料知识 | 更严重的错误 topic 默认；不是单纯资料不足拒答 |
| 7 内部异常 | 缺 quote、编造 quote、互斥输出三种合法 JSON 但非法意图 | 均得到截图完全相同的“请先明确：暂时没能完整理解…” | 确认模板可由不同校验失败产生，不能据模板唯一反推出具体原因 |
| 7 技术故障 | HTTP 429、transport timeout、MODEL_OUTPUT_INVALID、超预算本地等待、工具 timeout | 分别 unavailable/budget_exhausted；不是“四味菜没有资料” | 当前处理区分部分技术故障；但 JSON 内容错误可能被显示成“连接中断”，仍有错误归类问题 |
| 8 年龄闲聊 | 固定合法 conversation | conversation/answered，0 工具 | 通道正常；另有历史真实 age 回执证明该会话的闲聊可完成 |
| 9 质疑上轮 | 前轮固定校验失败、0 工具；后轮注入“资料未调出”解释 | payload 无上一轮工具状态，conversation 原样返回该解释 | 确认系统没有验证内部原因；历史实际出现过这一问题，但 Mock 不证明自然生成概率 |
| 10 景区命名 | 清明上河园 focus、固定 query | direct/needs_data，将文化命名问题当成详细介绍资料待添加 | 不局限于美食 |
| 补充：自由回复边界 | 注入虚构营业、步行时间文字到 conversation | 无工具也被原样返回 | 证明当前自由回复缺机器事实检查，不代表 Kimi 实测必然编造 |

每条轨迹保存接口、模式、事件、工具状态码、固定决策、当步合法工具、Prompt 哈希与策略标记、上下文键、focus、历史条数、fallback 和最终回复生成组件。未保存用户运行库的原始对话。

另运行现有相关回归：

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  backend/tests/test_agent_conversation.py \
  backend/tests/test_agent_provider.py \
  backend/tests/test_model_schedule.py -q
```

结果 **68 passed**。这些用例通过与知识缺陷并存，说明既有“能解释能力、能承接追问”的测试不能替代菜品、文化及知识/业务混合场景验收。

## 4. 直接相关的具体位置与触发条件

### A. 过宽的知识限制

`backend/app/city/agent/understand.py:122-123`：

> 不提供context没有的景点介绍、地址、票价、营业时间、演出时刻、商家、交通耗时或路线事实；不使用模型记忆补齐。

后面要求这类查询交给 `actions.query`。这里没有把一般历史/文化介绍与实时、地点相关的业务事实分开。该句明确列了景点介绍，但没有逐字列“所有菜品知识”；不能把它表述为代码中明确存在“禁止任何食物常识”的单独规则。结合 query/详情兜底及历史后续答复，可以确认它对旅游知识使用形成了过宽限制。

### B. query 没有知识目的，详情缺事实统一挡住介绍

`assistant/interpret.py:14-26` 只有 next/history/progress/modify/shows/travel/dining/reference/trip，没有 knowledge。`agent/service.py:400-402` 对模型 query 又按**原始文字**重跑本地规则；`Action.target_name` 不在此路径中消费为独立知识查询对象。

`exploration.answer_focus:263-266` 同时检查 focus 和有限词语。命中“这道/这个”等且 `fact_id` 为空，就把介绍、实景、营业资料统一返回 needs_data。即使 fact_id 存在，也没有通用文化介绍分支，最终落到 `:283` 的待添加回复。没有命中指代词的“四味菜为什么叫四味菜”则可能跳过该函数，落到 trip 默认目的。

`assistant/service.py:136-146`：没有正式路线就让用户先补要求，有正式路线就列剩余活动。知识问题因此可能完全答非所问。

### C. ReAct 只做下一步选择，没有知识回复出口

`agent/service.py:306-310` 要求 JSON 工具选择、不输出解释、无数据停止；`:339-345` 的 finish 是固定模板，不是模型自然语言总结；`:462-465` 对 needs_data/conflict 直接终止。`no_candidates` 可由下一步 finish 收尾，但仍无知识部分。

`agent/models.py:78-97` 禁止 `conversation` 与任何 business actions 同时出现，Prompt 最后也重复此规则。这个约束原本用于防止聊天偷偷执行操作，但同时阻断了合法的“知识介绍 + 只读商家查询”表达。不能简单删除互斥规则来修复，否则会连行程修改边界一起放开。

### D. 上下文有对象名称，却没有知识正文与内部诊断证据

`basket.resolve:65-87` 对本地 food ID 可解析“四味菜”名称；无绑定事实时 `fact_id=null/data_status=pending_addition`，并不等于模型不知道这道菜是什么。当前运行库确实没有四味菜 food record 或 binding，但这只是当前快照。

`assistant/context.py:39-58` 是**最近 8 条消息（通常 4 个来回），不是最近 8 轮**；还构建最多 600 字的摘要。`agent/conversation.py:19-42` 最终送给理解模型的是当前条件、progress、focus、basket、recent_messages、readiness 等，**没有 envelope.facts、summary 或上一轮 trace/error**。因此本轮不能归咎于该 Prompt 实际没有接收到的会话摘要；也不能把历史自然语言解释当作内部工具事实。

当前 readiness 的缺数据提醒适用于路线能力/下一步，但缺少明确的“这不妨碍一般文化知识解释”的例外。这是高怀疑的额外引导因素；Mock 无法测定它对真实模型的独立影响。

### E. 校验失败误装成用户没说清楚，且丢失子原因

`understand.py:152-177` 把 Pydantic ValidationError 与原文/字段 ValueError 合并为同一 unresolved 文案，`service.py:396-397` 加上“请先明确”。这是模型/协议未通过校验，却让用户承担“你的问题不明确”的体验。

失败后 event 仍可为 `status=ok`，只通过 `fallback=true` 区分（`service.py:390-395`）；具体校验错误没有写入安全诊断字段。正常 JSON 但结构错误、引用错误、互斥冲突都可能产生截图模板，原始失败不可再细分。

技术错误并非一概变成资料不足：HTTP 429、超时等有独立终止状态。但当前 `MODEL_OUTPUT_INVALID` 也可能走“刚才连接中断了”路径，说明需要把网络、协议、内容校验分开，而不是统一换文案。

### F. 自我解释没有可追溯约束

`Conversation` 只有 `reply` 的类型与长度检查（`models.py:74-75`），`service.py:398-399` 直接回传，没有验证“查询失败/数据库没有资料”等内部过程断言。历史回复文本虽标为不可信，但并没有可供模型对照的上一轮安全状态摘要。单改道歉语不能解决此缺口。

## 5. 影响范围

- 美食特色、常见做法、名字来源与知识追问会受到影响。
- 景区历史、文化命名、一般介绍同样可能受影响，S10 已确认相应分支。
- 有无正式行程会改变错误回复，可能从“缺资料”变成活动清单。
- 知识与查店/规划混合请求缺少完整表达和部分成功回复机制。
- 一般闲聊已有正常通道，不能说“所有对话都不能用”；但依赖自由回复的内部原因和业务事实目前没有内容级校验。
- 本轮不评价 Kimi 对四味菜的真实知识准确性，也不声称真实商家、营业、演出、交通或路线已验收。

## 6. 最小修复建议（下一轮，未实施）

延续当前单 Agent、provider、调度和确定性服务，不换模型、不引入新框架、不改 OR-Tools。

| 补丁 | 拟改文件 | 最小目标与约束 |
|---|---|---|
| 区分知识与实时业务 | `agent/understand.py`、`agent/models.py` | 给一般文化/美食知识明确只读表达；允许模型已有稳定知识，无法确认的传说、配方差异等保留不确定性。营业、供应、距离、场次、价格、交通和可执行路线仍交可靠服务 |
| 知识/业务混合部分成功 | 同上及 `agent/service.py` | 知识片段与只读查询动作分别表达；商家失败保留知识片段。只对知识+只读查询放开组合，禁止与 progress、position、modify、planning 或确认混用；沿用现有 reply/answers 展示，原则上无需改前端 |
| 避免知识进入 trip 默认兜底 | `assistant/interpret.py`、`assistant/service.py`、`exploration.py` | 仅在已判定的知识路径绕过业务详情缺数模板；保留原营业、场次、供应和路线校验。不要在默认“trip”里按词汇补几条四味菜答案 |
| 安全诊断状态与错误类别 | `agent/understand.py`、`agent/service.py`、`agent/conversation.py` | 保存有界的 schema/grounding/mutual_exclusion 等类别和上一轮工具状态摘要，不保存原始输出、位置、Key 或完整请求；模型不再凭聊天文本推测内部调用。对无证据的问题明确承认原因无法确认 |
| 契约与回归 | `scripts/export_agent_contracts.py` 输出、相关测试 | 新只读字段更新 schema，并逐项验收混合、失败、历史和原确认边界；诊断断言需改成期望行为断言，不能拿本轮 19 passed 当修复结果 |

关键决定：**不是全局取消“不得编造事实”，而是按内容类型用不同证据要求。** 稳定旅游文化知识可自然解释；具体门店、位置、营业、场次、交通和正式状态仍必须来自可靠业务工具。知识文字不能写入事实目录、商家关系、路线或长期偏好，也不能作为路线求解依据。

补丁设计可以保留原来的防编造商家、演出、交通与确认机制，因为那些逻辑不需要改动。但当前 conversation 已有仅靠 Prompt 的事实约束，本轮固定输出对照证明它不是完备的机器真实性过滤器。不能承诺“改一个 Prompt 绝不会影响边界”；必须以以下反例和业务回归验证。

## 7. 建议新增的验收回归

1. 本轮 10 场景转为正向要求：知识不依赖店铺目录，业务不编造，混合请求部分成功，规划不因知识文本发生修改。
2. 同一知识问题对照：空/有目录、无/有正式路线、有/无 focus、有/无上一轮行程要求。知识输出不得被剩余活动清单替代。
3. 真实 `page_focus` 传递与完整名称/指代的端到端测试；不仅检查回复是否含“四味菜”，还检查分类、回复来源、实际工具与状态。
4. 商家无结果、营业过期、演出取消、交通缺边时继续拒绝业务断言；知识内容不得带出未经查询的具体店铺、价格、距离或时刻。
5. 知识与只读查询组合可完成；知识与自动路线确认组合必须拒绝。保留版本、权限、失效预览、取消和显式确认回归。
6. 模型 JSON 错误、互斥输出、引用不匹配、429、超时、请求排队和工具超时分别显示正确错误类型，不能说成用户没讲清或数据库没资料。
7. 前轮无工具时，后轮不可宣称某工具失败；前轮工具返回 no_candidates 时，也不可扩展成“不知道这道菜是什么”。关闭历史和跨旅程不得泄漏诊断内容。
8. 若要证明真实 Kimi 的分类和自然答复改善，需要下一轮另做明确预算的实测；Mock 不能替代语言质量验收。

### 可选的真实模型最小计划

本轮执行 0 次。若下一轮确需确认语言质量，可先审查合成上下文，使用隔离空库、公开固定问题和合成历史，最多 4 次请求：菜品知识、承接知识追问、知识+找店、质疑一次合成校验失败。复用现有调度与 45 秒产品预算，不自动重试，不发送真实对话、用户位置或原行程。检查当前限流再安排顺序，不增加预算绕过限流。此计划仅列出，未获执行指令、未发请求。

## 8. 本轮改动、验证与未完成项

新增内容仅为本报告、两份 `.py.txt` 诊断程序、源码哈希基线及脱敏 JSON 证据；没有改生产 Prompt、前后端业务代码、配置、正式数据库或确认机制。测试库全部为临时合成库，未导入运行库。没有重启或停止 8017；诊断前后启动计数 5、重载计数 4 保持一致，监听进程保持一致。

诊断前后对 `backend/app` 和 `frontend/src` 共 122 个生产文件做 SHA-256 核对。最终核对结果见 `reports/knowledge-qa-source-check-20261023.json`。临时测试模块和数据库由 TemporaryDirectory/pytest 清理。

已完成：历史回执核对、实际服务路径审计、十场景受控复现、错误与工具边界对照、最小补丁建议。未完成且不在本轮授权内：生产修复、真实模型新增对照、实际四味菜知识正确性与真实商家数据核验。原始模型 JSON 和校验子错误未留存，无法通过现有证据补回。
