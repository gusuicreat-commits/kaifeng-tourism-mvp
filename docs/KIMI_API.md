# 小汴梁 Kimi API 接入

> 2026-10-23增量：本页原有单景区 API 与700 token说明只适用于旧解析通道；城市 V2/V3 已接通，V3有独立预算。所有模型入口使用共享的进程内请求调度，首次理解等待规则见 [连续对话修复](reports/agent-conversation-repair-20261023.md)与 [V3.4](AGENT_V3_RATE_LIMIT_AND_LIVE_ACCEPTANCE.md)。不是跨进程限流或生产容量验收。

2026-10-21。复用现有 `backend/app/language.py` 的 OpenAI-compatible HTTP 接口，未新增 SDK、Agent 框架或前端改动。

## 本地配置

实际密钥仅放在项目 `.env`，文件权限为 `0600`，`.gitignore` 已排除它；源码、报告与 `.env.example` 不含实际密钥。后端读取：

```dotenv
TOURISM_LLM_BASE_URL=https://api.moonshot.cn/v1
TOURISM_LLM_MODEL=kimi-k2.6
TOURISM_LLM_API_KEY=在本地私有配置填写
```

按[官方快速开始](https://platform.kimi.com/docs/get-api-key)使用 `/v1/chat/completions`。本次选择 K2.6 非思考模式，适配短 JSON 需求解析；移除旧的 `temperature=0`，因为 K2.6 固定温度、不接受该值，见[官方模型参数](https://platform.kimi.com/docs/api/models-overview)。JSON Mode 不代替后端 Schema 校验。

## 实际作用与边界

- `/api/ai/interpret` 将明确需求解析为有类型的偏好候选，校验日期、时间、名称及字段；仍要求确认。
- 已有 `/api/v1/chat` 在需要时调用同一解析器，携带原有受限的单景区 M7 上下文；模板回答和确定性修改仍可绕过模型。
- `/api/ai/explain` 仅允许模型选择已经计算好的事实句，失败回退模板。
- 模型不执行 SQL、路线算法、场次生成或行程提交。城市聊天与规划工具由后续 V2/V3 接入，见 [CITY_AGENT_V3](CITY_AGENT_V3.md)。
- 请求超时12秒、生成上限700 token、不自动重试生成；上游错误、非对象/非法 JSON、截断结果或工具调用输出均不进入业务修改，返回安全错误。
- `/api/ai/status` 的 `available` 表示配置齐全，不代表余额、实时服务健康或模型语义准确率。真实连接证据另列报告。

## 验证

```bash
.venv/bin/python scripts/check_kimi_connection.py
# 显式联网：查询模型列表，再最多生成一次；可能产生 API 费用。
.venv/bin/python scripts/check_kimi_connection.py --live
.venv/bin/pytest -q
```

离线检查仅返回是否配置，不输出密钥。联网脚本固定官方端点、关闭重定向，只发送“游玩两小时”的合成短文本，不读取数据库、真实游客对话或当前位置；不打印上游响应正文、请求头或异常字符串。

本次模型列表 HTTP 200，所选模型可用；一次真实 JSON 提取返回120分钟，生成约798.8ms。全量后端回归296项通过（含14项新增兼容/故障测试），仅现有 Starlette/httpx 弃用提示。自动测试禁用本地真实 Key，采用 MockTransport，验证无网络配置、Kimi 参数、认证/限流、超时、截断及错误结构降级。单次成功不代表理解准确率或生产延迟指标。

运行服务加载及实际解析入口的验证结果见 [接入记录](reports/kimi-api-20261021.json)。真实服务的模型调用会产生供应商计费，平台余额和配额仍由平台管理。
