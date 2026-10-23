# 同地往返理解与书笺对话修复

2026-10-23。修改主项目 `kaifeng-tourism-mvp`，当前 5177 / 8017 开发热更新页；隔离副本未修改。未重写用户历史或向用户行程提交路线。

## 问题证据

截图中的“从开封站出发和返回”对应既有回执为 `needs_clarification`：Kimi HTTP 200，一次请求，约 3.21 秒，理解层 `fallback=true`、`provider_failure=null`。旧回执没有具体校验失败项或原始模型对象，因此只能确认结构或原文依据校验未通过，不能追溯具体字段。运行库地点事实数量为 0；这是无法计算路线的另一项限制，不应阻止记录游客报告的地点名称。

## 改动

- 后端对完整、明确的“从某地出发和返回/返程”提供窄范围确定性控制：两个端点共享原文中的地名。额外要求、疑问和含糊选择不做部分截取，继续原理解流程。地点名匹配不到时保存 `origin_text` / `destination_text`，不生成地点 ID、位置或正式路线。
- 提示模型同地往返的两个字段可以引用同一段地名；拒绝结果仅记录 schema/evidence/provider 分类和受限顶层字段，不保存原始模型输出。
- 城市聊天改为宋体、宣纸与淡青配色的左右气泡；记录区独立滚动，输入区留在面板底部。上翻时可“回到最新”；翻旧记录保留阅读位置，视口变化时跟随最新状态。
- 新增只读 `GET /api/city/v1/trips/{trip_id}/assistant/messages?before=...`。每页 40 条，按同会话、同行程、有效期及留存设置查询；展示历史与模型最近 8 条上下文分离。
- 手机助手形象缩小至输入区右侧预留位置，保留语音与拖动入口。

改动文件：

- `backend/app/city/agent/{planning_intent,understand,service}.py`
- `backend/app/city/assistant/api.py`
- `frontend/src/{cityAgentApi.ts,components/CityAgentChat.vue,components/AssistantLauncher.vue}`
- `backend/tests/{test_planning_understanding,test_agent_conversation}.py`
- `frontend/tests/assistant-dialogue.spec.ts`

## 验证

```sh
.venv/bin/python -m pytest backend/tests/test_agent_conversation.py backend/tests/test_planning_understanding.py backend/tests/test_city_agent.py backend/tests/test_city_assistant.py -q
npm run build --prefix frontend
cd frontend
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e -- --grep '书笺聊天|同地往返|手机连续问答|首次模型等待'
```

结果：142 项后端通过；5 项浏览器通过；TypeScript 和 Vite 生产构建通过。浏览器使用临时数据库、模型替身或明确地点控制，无真实供应商请求。检查 320px、465px、1200px 宽度、历史分页、输入区可见、无横向溢出、人物不遮挡记录、刷新后两个地点意向保留，以及不提交正式路线。

当前用户页面通过内置浏览器目视检查，并实际向上滚动出现“回到最新”按钮；截图 `docs/screenshots/assistant-chat-live-20261023.png`。合成记录布局截图 `docs/screenshots/assistant-chat-scroll-mobile.png`。

真实模型复现首次因网络受限未完成；申请真实网络执行后，自动审批拒绝向 `api.moonshot.cn` 发送固定测试句，理由为未获得该发送的明确授权。本轮未绕过审批，也未将模拟测试宣称为真实模型重验。该句的新确定性分支不调用模型，软件链路已经验证；通用自然语言的真实模型重验未完成。
