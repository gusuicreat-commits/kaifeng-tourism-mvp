# 小汴梁连续对话修复

2026-10-23。范围是城市助手的正常问答、追问及其与现有规划流程的衔接，未补入真实地点、商家或导航资料。

## 已确认根因

1. `agent.understand.parse` 把本地识别为 query 的话语直接跳过模型；模型本身又只能输出业务意图。通用能力问题和术语解释最终进入 V2 `_query` 的“尚无路线”兜底。
2. 模型输入遗漏最近对话，不能依据上一句解释；同时最初的空草稿和已保存规划条件缺少统一优先级。
3. 预览中的通用“请补充起终点”未区分游客缺信息和产品缺地点资料，模型容易反复要求游客解决资料缺口。
4. 连续消息碰到进程内模型间隔时，产品模式直接结束任务，无法承接下一轮。

## 最终实现

- 单次模型理解可以返回 `conversation` 或结构化业务意图。普通问答与解释直接生成简短回复；日期、时段等修改仍由原规划工作流校验和处理。
- JSON Schema 和 Python 校验同时约束对话与业务动作互斥。对话没有路线提交、素材修改或规划条件写入权限，不继承待处理业务修改。
- 上下文使用当前旅行最近 8 条脱敏消息，并以已保存规划条件覆盖初始草稿。关闭留存或换旅行后不带入旧消息；历史中的指令和日期不能冒充当前原文依据。
- 只读上下文明确区分 `missing_user_fields`、`missing_data_fields` 和实际下一步；缺地点库时不把内部预览的通用催填文本当作对话依据。
- 下一站、明确的已保存行程汇总与剩余活动查询继续由确定性服务直接回答；具体营业、演出、交通等查询仍使用现有事实服务。
- 仅首次话语理解允许等待本地间隔、RPM窗口或并发许可，仍受原 45 秒预算约束。前端可取消等待；恢复前重查版本。复杂工具探索的无许可行为保持原规则，上游失败不自动重试。
- 真实连接失败返回“刚才连接中断了，请再试一次。”，不再当作用户表达不清。

## 验证

- 最终相关后端回归 **186 项通过**；覆盖对话、多轮上下文、规划、原有 Agent、调度、事实查询、素材协商、取消和确认。
- 80 条既有离线语料：字段覆盖 80/80、路由 80/80。它是开发者编写的回归集，不是通用语言准确率评估。
- 浏览器 20 项分批通过：`assistant-dialogue.spec.ts` 3、`detail-basket.spec.ts` 10、`agent-v3.spec.ts` 7。新连续对话及等待/取消检查使用 465×769 手机视口；模型为显式隔离替身。
- `npm run build --prefix frontend` 与 `scripts/export_agent_contracts.py --check` 通过。
- 真实 Kimi：临时空数据库、固定四句合成输入，4 次实际请求，未提交正式路线。最终结果为 `passed`，见 [机器报告](agent-dialogue-live-20261023.json)。这是当前句组的真实联调证据，不是任意问答质量或真实开封规划验收。

真实测试的最后一句“那我接下来该做什么？”得到了基于已保存日期的时段补问，并明确“即使时段齐了，目前也暂时排不出具体路线”。对话没有再落到原来的路线兜底模板。

过程中曾出现模型混合两类输出、本地间隔停止、回答未遵守缺资料边界，以及一次后续追问连接中断。相应失败报告保留在本目录的 `agent-dialogue-live-20261022-initial.json`、`agent-dialogue-live-20261023-spacing.json`、`agent-dialogue-live-20261023-routing-only.json`、`agent-dialogue-live-20261023-data-grounding.json`、`agent-dialogue-live-20261023-network-interrupted.json`；后两个的自动结果与人工质量检查须分别阅读。失败没有触发运行库写入或上游自动重试。

用户运行页已重新加载，未代发真实用户对话到模型，也未删除用户旧聊天。当前运行服务为 5177 / 8017 的开发热更新模式。代码备份与校验值位于 `.backups/conversation-20261022/manifest.json`。

## 复验入口

```sh
.venv/bin/python -m pytest backend/tests/test_agent_conversation.py backend/tests/test_planning_understanding.py backend/tests/test_city_agent.py backend/tests/test_model_schedule.py backend/tests/test_agent_provider.py backend/tests/test_agent_live_budget.py backend/tests/test_city_assistant.py backend/tests/test_exploration.py -q
.venv/bin/python scripts/verify_agent_dialogue.py --check-only --output /tmp/dialogue-check.json
.venv/bin/python scripts/verify_agent_dialogue.py --live --output /tmp/dialogue-live.json
```

`--check-only` 不请求外部模型；`--live` 每次最多 4 个实际请求，输入固定，使用新建临时数据库，不读取运行中的旅行或聊天。前端截图 [assistant-dialogue-mobile.png](../screenshots/assistant-dialogue-mobile.png) 来自隔离替身测试，不是真实用户或真实模型界面截图。
