# 小汴梁 V3.3 真实 Kimi 联调与验收

> 历史阶段记录：仅描述原日期的软件、操作与验收边界，不作为当前开发待办或最新完成声明。当前状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。

日期：2026-10-21。**最终验收：BLOCKED。** 软件保护、离线规划与确认通过；真实 Kimi 全链尚未通过，阻塞证据是本轮实际返回的组织级 `max RPM: 3`。所有旅游资料均为隔离合成样本，不构成真实开封运营验证。

## 六项结论

1. **之前为什么出现 429？** 旧日志只保存 HTTP 状态，无法追溯旧请求的具体原因。本轮复现明确返回 `rate_limit_reached_error`、`organization max RPM: 3` 和 `Retry-After: 1`，确认本次为组织速率限制。不能反推此前每一次 429 都是同一原因，也没有证据认定欠费或服务拥堵。
2. **之前为什么提前规划？** 受限 JSON 协议仍允许模型产生错误的下一步建议。旧联调中模型在商家、演出及交通证据不足时选择 `preview_plan`；服务端已拒绝。V3.2 后续增加逐步 `eligible_actions` 和动态 tool 枚举，V3.3 保留这些门禁并补齐审计。模型建议不是工具授权。
3. **修复到什么程度？** 已能区分三类官方 429，记录脱敏摘要、等待要求、数值限额、请求 ID（供应商提供时）、模型、域名、耗时和 usage。没有 SDK、隐式重试或重定向；每次适配器调用只有一次 HTTP 尝试。非法提前预览可在一次修正预算内恢复，否则停止；Mock 注入两次非法预览时工具执行数为 0。尚未证明真实 Kimi 能稳定纠正该错误。
4. **真实 Kimi 完整执行过一次吗？** 本轮没有。健康预检、需求理解、第一次合法行动选择成功；搜索工具查到 1 家合成候选，下一次模型请求触发 429。后续演出查询、交通核验、规划预览和真实模型候选确认未完成。没有将确定性 fallback 算作模型成功。
5. **候选能否安全确认？** 隔离软件验收通过：只有显式测试用户确认才提交；候选确认后 trip version 从 5 到 6、route version 从 1 到 2，已完成记录和锁定演出保留。取消后不可确认，其他页面改版本后旧候选返回 409。真实 Kimi 本轮未产生候选，因此这不属于真实模型全链确认通过。
6. **下一阶段还需要什么？** 先在当前中国站账户核对组织 RPM/TPM/TPD、并发、模型权限与平台用量，使其能容纳约 5 次模型请求在 45 秒内完成，健康预检另计；其他应用共享组织额度也须计入。随后显式重新运行真实 A/B 联调。未更换账户/模型、购买额度或充值。真实商户、场次、道路、交通及游客效果另行验收。

## 本轮代码审计与改动

请求路径：`city.agent.understand` / `city.agent.service._decision` → `city.agent.provider.generate` → `httpx.Client.post('/chat/completions')`。沿用 `.env` 中现有 `kimi-k2.6`、`api.moonshot.cn`，非思考 JSON 输出；没有使用 OpenAI SDK。旧景区/V2 的 `language._call` 并非本次 V3 ReAct 请求通道，本轮未修改它。

| 文件 | 本轮内容 |
| --- | --- |
| `backend/app/city/agent/provider.py` | 单次请求诊断、三类 429、Retry-After 秒数/日期、请求 ID、数值限额、usage 保留及脱敏 |
| `backend/app/city/agent/understand.py` | 理解结果标记来源；解析失败保留诊断并标记确定性降级 |
| `backend/app/city/agent/service.py` | `decisions` / `stop_reason`；合法动作、模型选择、合法性、观察摘要、工具状态、请求序号与来源进入受控轨迹 |
| `scripts/agent_live_diagnostics.py`（新增） | 评测串行计数、最小间隔、请求总上限、供应商失败后停止本次在线运行 |
| `scripts/evaluate_agent_v3.py` | 原评测复用同一请求预算控制 |
| `scripts/evaluate_agent_v33.py`（新增） | 独立合成库的健康预检、A 成功链、B 缺供应对照、独立确认及失败报告 |
| `backend/tests/test_agent_provider.py` | 429 分类、脱敏、等待解析、用量保留、数值限额和单次请求验证 |
| `backend/tests/test_city_agent.py` | 来源区分、非法提前预览、观察变化、理解失败、过期候选确认保护 |
| `backend/tests/test_agent_live_budget.py`（新增） | 请求上限、失败计数、停止开关与间隔控制 |

未改变求解器、业务硬约束、工具前置条件、确认接口、M7 权限或删除台账。默认仍为 4 个工具、6 次模型请求、45 秒任务、12 秒单次超时、1 次修正。新增响应字段为兼容性补充；三份输入契约检查一致。

轨迹来源严格区分 `live_model`、`mock_model`、`deterministic_fallback`。正常无模型规则处理也归入确定性来源；不因模式名为 `react` 就视为真实模型执行。日志不记录完整内部推理、完整请求正文、凭证、原始坐标或账户标识；仅记录受限动作及必要业务摘要。启用留存才写入现有 M7 审计，关闭留存继续遵守既有清理逻辑。

## 真实执行证据

原始脱敏报告：[agent-v3-kimi-live-run-20261021.json](../../reports/agent-v3-kimi-live-run-20261021.json)。本次仅一轮在线运行，收到 429 后没有继续发起请求。

| 全局请求序号 | 任务内序号 | 内容 | 结果 | 耗时 |
| --- | --- | --- | --- | --- |
| 1 | — | 最小 JSON 健康预检 | HTTP 200 | 974.86 ms |
| 2 | 1 | 复杂旅游需求结构化理解 | HTTP 200，原文/Schema 校验通过 | 4256.60 ms |
| 3 | 2 | 选择下一行动 | HTTP 200，选择合法 `search_dining` | 1281.93 ms |
| 4 | 3 | 搜索得到 1 家候选后的下一行动请求 | HTTP 429，`rate_limit_reached_error` | 336.20 ms |

第一次选择时合法集合为 `finish`、`clarify`、`search_dining`、`query_shows`，没有开放 `preview_plan`。餐饮搜索返回 `ok`、1 个有合成供应及营业依据的地点。请求 4 尚未返回有效动作，不能声称模型已经根据搜索结果成功完成后续决策。任务最终 `unavailable`，停止后显式取消，正式状态保持不变。

- 总供应商请求 **4** 次，其中任务内 **3** 次；3 次成功响应、1 次 429；自动重试 **0** 次，并发 **1**。
- 请求开始间隔约 **2.005 / 4.289 / 2.005 秒**。2 秒是本轮初始保守间隔，并未满足后来才确认的 3 RPM 限制；不宣称已解决账户吞吐。
- 供应商错误摘要保留 `organization max RPM: 3`；`Retry-After` 原值 `1`，解析为 1 秒。遵守停止策略，没有立即重试，也没有把该提示当作长期可用 RPM。
- 已返回用量：prompt **1910**、completion **154**、total **2064 token**；429 未提供 usage，未补零冒充完整计费凭据。
- 任务墙钟约 **7413 ms**，不是 45 秒预算耗尽。费用 `null`，没有获取账单和核验价格快照。
- 本次响应未提供受支持响应头中的 request ID，记录 `null`，没有伪造。
- 首次在线采集未把完整结构化理解对象单独写入报告，保留了校验结果和下一步轨迹；后续脚本已补充该输出字段。在线运行之后仅补充诊断脱敏、限额提取及报告字段，没有再次消耗真实配额。

3 RPM 的持续调用能力与约需 5 次模型请求/45 秒的成功任务不匹配，健康预检还会占一个名额。仅等待 `Retry-After: 1` 或把总请求上限改成 12 均不能解决这一矛盾；没有擅自放大任务预算。上次 9 次真实请求属于历史轮次，不计入本次 4 次。

官方分类核对：[Kimi API 故障排查](https://www.kimi.com/help/kimi-api/api-troubleshooting)。官方分别定义组织限速、服务拥堵和账户配额问题；本轮诊断以实际错误体为准。网页阅读通道因网络限制失败后，使用网页工具读取了同一官方页面。

## 离线、Mock 与浏览器验收

| 验证 | 实际结果 | 证明范围 |
| --- | --- | --- |
| 改前后端基线 | 374 passed | 当前仓库旧功能基线 |
| 最终完整后端 | **390 passed，1 warning** | 比基线新增 16 项；不是游客准确率 |
| 专项 Provider / Agent / 预算 | **59 passed** | 包含三类 429、未知 429、401、超时、非法 JSON、循环和提前预览 |
| 三份契约检查 | 全部通过 | Agent、规划引擎、城市助手 |
| 80 条开发期合成语言回归 | 字段覆盖与路由 80/80 | 人工构造的回归语料，非独立真实标注 |
| V3.3 合成 A/B（禁用模型） | 通过 | A 预览并显式确认；B 供应事实改为未核验后 `search_dining → finish → needs_data` |
| Mock A/B | 通过 | 替身根据观察结果切换动作；不证明真实 Kimi 的同类能力 |
| TypeScript / 隔离 Vite 构建 | 通过 | 输出 `.test-results/agent-v3-dist`，未覆盖运行 dist |
| Chrome 浏览器 | **4 passed，4.6 秒** | HTTP/临时合成库：确认、取消、澄清、缺供应；模型禁用 |
| 真实 Kimi A | 中断 | 理解、第一次行动成功；搜索后的请求受限 |
| 真实 Kimi B/C | 未完成 | B 未启动；C 使用 Mock 验证，不能计作真实模型通过 |

现有 Starlette TestClient/httpx 弃用警告仍存在，未为本任务扩展依赖迁移。375px 截图已目视检查，不以此宣称全面视觉验收。浏览器首次尝试因沙箱禁止绑定 8021 失败；获执行环境放行后运行通过，未操作 8017。

正式状态保护包括：确认前不变、显式确认才提交、取消候选后返回 409、其他页面修改后旧候选返回 409、取消计算中迟到候选、锁定目标/完成历史保留，以及会话、CSRF、留存、导出和删除回归。

## 环境保护

- 本轮所有数据写入 `TemporaryDirectory` 的测试库及独立删除台账，不使用运行库。
- 不重启 8017、不切换运行构建、不修改前端源码、`frontend/dist` 或 `.env`。
- 相对本轮最早快照检测到前端并行改动，原样保留。随后构建/测试前后重新计算源码与运行 dist 指纹，全部一致；不能把外部并行改动算作本轮修改或用备份覆盖。
- 源码保护快照 `.backups/agent-v3.3-20261021/` 保留；原删除台账及数据库未恢复。
- 验证生成的手机截图更新在原 V3 截图位置，测试构建位于独立目录；都不是线上发布。

## 复验命令及前提

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp
.venv/bin/pytest -q
.venv/bin/python scripts/export_agent_contracts.py --check
.venv/bin/python scripts/export_engine_contracts.py --check
.venv/bin/python scripts/export_city_assistant_contracts.py --check
.venv/bin/python scripts/evaluate_agent_v3.py --output docs/reports/agent-v33-corpus-offline-20261021.json
.venv/bin/python scripts/evaluate_agent_v33.py --output docs/reports/agent-v33-offline-20261021.json
```

只有在当前组织速率条件满足任务需求后，再显式开始新一轮在线运行；当前报告没有后台重试任务：

```bash
.venv/bin/python scripts/evaluate_agent_v33.py --live --request-cap 12 --interval 2 \
  --output docs/reports/agent-v3-kimi-live-new-run.json
```

`--interval` 可取 2–60 秒，但不改变 45 秒任务预算，不能把增加间隔当作当前 3 RPM 条件下的完整解决办法。需要同时完成真实 A 候选、独立确认和 B 观察对照，保留所有合法/非法动作及停止轨迹；本轮不替未来结果提前写 PASS。
