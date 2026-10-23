# 小汴梁 V3.4 统一请求调度与真实闭环验收

2026-10-22。**验收结论：PASS。** 真实 Kimi 在独立合成库中完成一条完整的有限 ReAct 链，并通过独立测试用户确认提交。不是 Mock 或固定流程成功，也不是开封实地导航验收。对照场景产生了不同工具策略，但因工具预算停止，不能计作第二条完整成功。

机器报告：[agent-v3-rate-limit-and-live-acceptance.json](reports/agent-v3-rate-limit-and-live-acceptance.json)。完整真实轨迹：[agent-v34-kimi-live-20261022.json](reports/agent-v34-kimi-live-20261022.json)。

2026-10-23 增量说明：为支持连续聊天，**首次话语理解**碰到本地 request spacing、RPM window 或 concurrency 时，可在原45秒预算内返回 `running / waiting_model`，由手机前端等待后继续，可随时取消；上游冷却或工具探索阶段仍按下文规则停止，无上游自动重试。下文“产品模式无许可即停止”的结论是 V3.4 当轮行为。见[连续对话修复](reports/agent-conversation-repair-20261023.md)。

## 直接回答八个问题

1. **3 RPM 与45秒怎样协调？** 游客默认仍45秒；没有许可立即结束为 `model_busy`，保留正式行程。显式的隔离验收模式每任务最多240秒，容纳25秒间隔；工具4次、模型6次、修正1次均未增加。成功主任务实际125.677秒，所以不能声称3 RPM下原来的45秒产品模式也能完成同一复杂任务。
2. **共享调度实现到哪里？** V3理解/决策、V2城市聊天、旧景区聊天、自然语言解析/解释与Kimi检查脚本，均经过同一个 `model_schedule.request`。共享保证只覆盖同一Python进程；多个worker、设备、CLI进程和其他应用之间不共享窗口。
3. **普通游客会被迫等两三分钟吗？** 不会。业务调度不sleep、不建立后台队列；繁忙时即时返回，用户可以保留已有行程或使用确定性规划。240秒配置不由游客HTTP请求指定，仅测试驱动器在隔离子进程显式开启。
4. **真实链是否完成？** 完成1条：理解 → 餐饮搜索 → 演出查询 → 交通核验 → OR-Tools完整预览 → 显式测试用户确认。主任务5次真实模型调用、4个工具，正式路线版本1→2。
5. **是否观察驱动？** 是。每个模型请求均携带当时的合法动作和已返回观察。对照撤去菜品供应证据后，Kimi先得到no_candidates，再选择带 `relax_optional_food=true` 的搜索，随后核验交通；与主任务直接进入预览不同。对照用完4个工具后安全停止，没有预览或提交。它证明本次动作受观察影响，不证明任意请求都能成功或具有统计显著收益。
6. **仍须用户确认吗？** 是。模型没有正式提交工具。独立用户确认才将trip version 5→6、route version 1→2；重复同一确认返回成功且不二次写入，取消和版本变化后的候选返回409，完成历史和锁定演出保留。
7. **测试多少项？** 本轮基线399；最终后端428项、专项134项、浏览器5项通过，另有80条合成口语回归和三份契约检查。详见下表；专项是全量的子集，不能相加。
8. **可以停止架构扩张吗？** 可以冻结当前MVP Agent架构，转向真实数据、现场路线与产品验收。3 RPM容量、单进程运行和扩展搜索可能耗尽预算仍是明确限制；不意味着已具备多游客生产容量或真实城市导航能力。

## 审计发现与改动边界

改动前存在两条直接Kimi POST通道：`city.agent.provider.generate` 与 `language._call`。前者负责V3理解及ReAct，后者负责旧解析、路线解释与V2聊天；它们没有共享许可。`check_kimi_connection.py` 还会独立GET模型列表，随后立即生成。V3只有45秒墙钟检查，没有等待状态；V3.3评测包装只管理自己的请求间隔，不能协调其他入口。

现在两条POST路径都调用 `model_schedule.request`，Kimi检查脚本改为一次合成生成，避免“查列表后立即生成”的独立请求。ASR使用另一供应商，不属于本Kimi调度范围。客户端仍为直接httpx，没有OpenAI SDK、HTTP重试循环或重定向；单次网络超时维持原12秒配置。

启动脚本默认单worker，开发reload不等于共享多个worker的状态。只读进程/端口核对时未发现8017监听；没有主动启动、重启或终止服务。本轮TestClient和8021浏览器服务均为独立临时进程，不能当作当前8017已经升级。

## 调度机制

`backend/app/model_schedule.py` 是唯一发送许可入口。进程锁保护准入，**25秒最小间隔＋60秒滑动请求窗口＋并发1** 同时生效；不能瞬间突发3次。只在取得许可、即将发送时增加实际尝试数，本地等待不增加次数、不调用网络。

- 内部使用单调时钟判断间隔、冷却及窗口，展示 `next_allowed_at` 使用UTC带时区ISO时间；不能以系统墙钟回拨绕过等待。
- 返回等待理由、下一许可时刻、延迟和 `http_attempts=0`，不保存请求正文、会话、Key或Cookie。调度器不sleep、不持有HTTP线程等待许可。
- 可显式配置RPM、间隔、TPM和TPD；请求前保守预留UTF-8字节上界、协议余量及输出预算，完成后按供应商usage核销，缺usage保留预留值。不是精确tokenizer计数。
- 采纳支持的合法数值限额/剩余额度响应头；缺头不当作无限额度。可从429摘要读取明确的RPM/TPM/TPD数值。未知TPM/TPD保持null，本轮两者都未从供应商确认。
- TPD仅是当前进程的UTC日计数，重启丢失；不能替代供应商的全组织、全日账本，也不声称知道其实际日界线。
- 真实429记录分类、Retry-After、请求ID（如有）和用量；进入至少60秒、且不短于有效Retry-After的冷却。本任务不自动重发失败请求。组织限速和服务拥堵继续分别记录。
- 余额不足、401/403、明确TPD耗尽停止后续发送，等待维护者处理并重新初始化，不自动切Key、换模型或购买资源。
- 在线驱动器额外设置全轮12次实际HTTP尝试上限；健康检查和全部子案例共享它。调用适配层但未获许可，不算供应商请求；没有隐式重试放大。

3 RPM来自V3.3实际返回的 `rate_limit_reached_error / organization max RPM: 3`。本轮按此保守配置发送11次均成功，不证明额度被提升，也不保证其他应用同时用同一组织时不会429。官方分类参考：[故障排查](https://www.kimi.com/help/kimi-api/api-troubleshooting)、[限流指标](https://www.kimi.ai/help/kimi-api/api-rate-limits)。国际站文档只用于说明指标，不能据此推定中国站当前账户档位。

## 任务状态与兼容性

保留start / step / get / cancel。新增响应 `phase`、`waiting`、`execution_mode`、`metrics.waiting_ms`，没有添加游客可写的模式或预算参数。

| 模式 | 墙钟预算 | 无许可时 | 谁负责等待 |
| --- | --- | --- | --- |
| product，默认 | 45秒 | `status=model_busy`，明确停止探索，正式行程不变 | 不等待，既有前端可读取result.reply |
| live_acceptance，显式隔离 | 180–240秒，本轮240 | `status=running, phase=waiting_model`，返回next_allowed_at | 独立测试驱动器 |

`Budget.max_seconds` 的可表达上界扩为240，默认值仍45。只有运维声明test/development、显式允许验收，并校验数据库位于声明的隔离目录内，才创建较长预算任务；脚本自行创建TemporaryDirectory。该目录校验不是自动辨认生产库的安全系统，隔离脚本才保证本次确实没有使用运行库。

等待不增加step、模型次数、修正或工具次数。相同request_id/expected_step重复提交只读当前任务，不重发；到了许可时间，需要以新request_id、同一个expected_step推进。调用方应按next_allowed_at决定时机，不能将重放同一幂等请求当作新的重试。

恢复时重查会话、旅行/路线版本、购物篮版本和事实指纹，随后才可能取得发送许可。等待中取消、关闭留存、事实更新或版本变化后不会继续请求；下一许可超出任务预算时直接停止。已发出的模型请求仍可能产生费用，不能承诺撤销或退款。任务仍是当前进程15分钟临时对象，不新增跨进程任务恢复能力。

测试驱动器以最多1秒的等待片段响应Ctrl+C，转换成任务取消；这是客户端驱动，不是服务端睡眠。前端源码和当前dist没有被本任务修改；验证期间检测到并行开发产生的8个前端源码/构建文件变化，原样保留，未用备份覆盖。

## 真实执行结果

全部数据是合成fixture，明确业务日期2030-05-01；并非真实开封菜单、场次、道路或交通。

### 主场景A

输入为任务指定的完整需求。理解保留软菜品、少走偏好和硬演出要求。真实模型动作如下，每次均由服务端合法集合与参数校验二次保护：

| 全局请求 | 动作 | 动作前观察 | 结果 |
| --- | --- | --- | --- |
| 1 | JSON健康预检 | 无游客上下文 | HTTP200 |
| 2 | 结构化理解 | 当前正式城市上下文 | 原文/Schema校验通过 |
| 3 | search_dining | 尚无工具观察 | ok，1家有供应及营业依据的合成商家 |
| 4 | query_shows | 餐饮搜索ok | ok，保留s2 |
| 5 | verify_travel | 餐饮与演出均ok | ok，使用有方向交通依据 |
| 6 | preview_plan | 匹配的交通核验ok | OR-Tools与独立校验通过，needs_confirmation |

任务实际125.677秒，其中等待97.960秒；模型5次、工具4次、修正0次。没有使用fallback。确认前正式状态不变，独立测试用户确认后trip version 5→6、route version 1→2；保留completed visit、锁定s2及less_walking偏好。相同确认幂等重放成功；之后另用确定性测试候选检查取消和版本变化，均409，正式路线版本不变。这两个后置保护检查不算新的真实ReAct行动。

### 对照B：供应依据不可用

输入不变，只把供应事实标为未核验。Kimi的5次请求为理解、search_dining、query_shows、放宽软菜品的search_dining、verify_travel。首次搜索返回 `NO_VERIFIED_SUPPLY_OR_OPENING`，模型观察后选择 `relax_optional_food=true`，与A的工具参数和后续路径不同。重复搜索参数不同，不是同一动作无限循环；硬约束没有被授权取消。

此时已经消耗4个工具，服务端在再次请求模型或生成预览前停止为 `budget_exhausted`。用时124.588秒，等待96.485秒，没有候选或确认，正式状态不变。该场景只证明观察驱动的策略变化和预算保护，不能声称缺供应场景完整规划成功。并不为此提高工具上限；后续可优化剩余预算提示和替代搜索体验。

### 全轮统计

- 真实HTTP请求11/12，成功响应11，429为0，自动重试0，并发1；没有为用满额度发第12次。
- 实际发送时间间隔25.035664–25.090742秒。
- 输入7819、输出405、总计8224 token；11次均返回usage。
- 两任务等待合计194.445秒，单任务分别低于240秒；全轮含两个任务，不能把全轮用时当作一个任务的预算。
- 完整真实成功任务1条；对照预算停止1条；未经确认的Agent正式变更0。
- 费用为null，未核验价格和账单；未更换Key/模型、充值或升级账户。
- 首次原始输出保留于源码备份。只修正了B安全停止摘要的统计口径，动作、观察、时间和用量不变；随后补充了“发送前被拒绝”的审计显示并离线验证，没有再次消耗真实调用。

## 软件验证

| 验证 | 本轮实际结果 |
| --- | --- |
| 改前全量 | 399 passed，1 warning |
| 最终全量 | 428 passed，1 warning；新增29项 |
| 调度器/Agent/城市上下文/Provider/请求预算专项 | 134 passed；为全量子集 |
| 80条开发期合成语言回归 | 字段和路由80/80；不是独立真实游客准确率 |
| Agent、引擎、城市助手契约 | 均通过；Agent预算上界更新，默认45不变 |
| 离线合成A/B驱动 | 通过；A预览/确认/重放，B停止；取消与旧版本候选409 |
| TypeScript与独立Vite构建 | 通过，输出.test-results/agent-v3-dist |
| Chrome浏览器 | 5 passed，7.3秒；含既有购物篮集成回归；模型禁用 |
| 真实Kimi | A完整成功；B策略变化后预算停止；11次请求均HTTP200 |

专项使用模拟时钟和MockTransport验证间隔/滑窗、并发、Retry-After、三类429、401/403、请求与token预算、未知usage、共享入口、等待幂等、取消、超时、版本/事实变化、留存清理、游客不能注入验收模式及发送前拒绝计数。原有非法预览、循环、独立求解校验、权限、事务和确认测试保持通过。既有Starlette/httpx弃用警告未在本任务中扩展迁移。

## 文件和环境保护

新增核心文件：`backend/app/model_schedule.py`、`backend/tests/test_model_schedule.py`、`scripts/evaluate_agent_v34.py`。修改两条模型发送路径、Agent状态/预算/理解错误传播、测试隔离、旧在线计数包装、Kimi单次检查脚本及Agent契约；没有重构规划引擎或新增框架依赖。

源码备份与前端指纹：`.backups/agent-v3.4-20261022/`。没有恢复旧数据库、修改M7删除台账、写入运行库、改.env、改前端源码或替换frontend/dist。验证期间的并行前端变化涉及 `cityApi.ts`、`explorationApi.ts`、`details.css`、`BasketPlanning.vue`、`CityAgentChat.vue` 及对应构建文件，均不是本任务改动。测试图像更新属于原有浏览器测试产物，不是发布。真实联调使用独立临时库和独立删除台账，退出即清理。

## 运行与后续边界

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp
.venv/bin/pytest -q
.venv/bin/python scripts/export_agent_contracts.py --check
.venv/bin/python scripts/export_engine_contracts.py --check
.venv/bin/python scripts/export_city_assistant_contracts.py --check
.venv/bin/python scripts/evaluate_agent_v34.py \
  --output docs/reports/agent-v34-offline-new.json

# 只有这条命令会实际使用现有Kimi配置，全部子案例合计最多12次。
.venv/bin/python scripts/evaluate_agent_v34.py --live --request-cap 12 \
  --interval 25 --acceptance-seconds 240 \
  --output docs/reports/agent-v34-live-new.json
```

默认调度配置为 `TOURISM_LLM_RPM=3`、`TOURISM_LLM_INTERVAL_SECONDS=25`；可另声明已知TPM/TPD。未知不填任意高值。普通服务不应设置验收模式环境变量；脚本只在自身子进程设置验收开关、test环境与临时DB根目录。

3 RPM不足以支持多个游客同时快速完成这种5调用复杂任务，不能把低频验收PASS当作生产容量验收。多worker/多实例部署前，可以用现有数据库原子许可记录协调请求窗口、租约和日用量，并同时解决任务状态/取消的共享；该升级尚未实现。本次没有引入Redis、任务队列、LangGraph或多Agent。

下一阶段应优先核验有限真实地点、商家供应、营业、日期场次、舞台、出入口和交通，接通当前运行版本后做完整产品走查；不应再靠扩大Agent框架替代数据验收。
