# 小汴梁模型 API 接入（Qwen / Kimi）

## 当前配置：Qwen3.7-Plus（2026-10-26）

本机 `.env` 已按用户要求从 Kimi 切换到百炼北京地域的 `qwen3.7-plus`，继续使用现有 OpenAI-compatible HTTP 通道，无新增 SDK。实际密钥只保存在忽略提交的 `.env`（权限 `0600`），不覆盖独立语音配置。

```dotenv
TOURISM_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TOURISM_LLM_MODEL=qwen3.7-plus
TOURISM_LLM_API_KEY=在本地私有配置填写
```

百炼[官方端点说明](https://help.aliyun.com/zh/model-studio/error-code)确认北京地域原域名仍可使用；业务空间专属域名需另提供真实 WorkspaceId。旧解析器和 V3 Agent 两个入口均显式发送 `enable_thinking=false`、`temperature=0` 和 `response_format={"type":"json_object"}`，沿用原有输出上限、服务端 Schema/证据校验和确认流程。思考开关见[官方说明](https://help.aliyun.com/en/model-studio/deep-thinking)，JSON 模式要求见[结构化输出](https://help.aliyun.com/en/model-studio/qwen-structured-output)。

连接检查仍沿用历史文件名：`.venv/bin/python scripts/check_kimi_connection.py --live`。脚本仅允许已登记的 Kimi 与北京百炼官方端点；不打印密钥或原始上游正文。V3 内部的 `kimi_validated` 是兼容保留的历史校验标记，实际供应商以回执中的 `model`、`endpoint_host` 和 `source` 为准。

本次验证：模型适配、调度与语义/规划回归共145项通过（Mock/临时库，1项既有弃用提示）；真实请求2次，分别为合成时长提取（约1.35秒）和临时隔离库的 V3 `/agent/understand`（约5.04秒，返回通过类型及证据校验）。两次均使用 `qwen3.7-plus`；未访问正式旅行库、未调用业务Provider、未提交路线。抽样结果不代表全部表达准确率或并发容量。

## 历史：Kimi 接入（2026-10-21）

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
