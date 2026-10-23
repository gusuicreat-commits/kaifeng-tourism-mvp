# 开发文档导航

核对日期：2026-10-23。先了解现状，再读任务涉及的专项说明；历史阶段报告不进入默认阅读链。

## 团队交接

[Windows接手步骤](../HANDOFF.md)、[团队数据待补清单](TEAM_DATA_GAPS.md)、[本次交接验收](reports/team-handoff-20261023.md)。

## 当前入口

| 文档 | 用途 |
| --- | --- |
| [PROJECT_STATUS](PROJECT_STATUS.md) | 已实现、真实数据、测试失败、剩余缺口及能力接口滞后事项 |
| [PRD](PRD.md) | 产品范围与正式V1完成要求 |
| [DEVELOPMENT_DIRECTION](DEVELOPMENT_DIRECTION.md) | 产品来源、方向取舍、实施差距、V01–V18与游客评价口径 |
| [项目AGENTS](../AGENTS.md) | 开发约束和真实性要求 |
| [CITY_ARCHITECTURE](CITY_ARCHITECTURE.md) | 当前代码接线、模块与控制权 |

## 专项说明

| 领域 | 文档 |
| --- | --- |
| 规划算法、接口及版本 | [ALGORITHMS](ALGORITHMS.md)、[PLANNING_ENGINE_V1](PLANNING_ENGINE_V1.md) |
| 交互与行中修改 | [PLANNING_WORKFLOW](PLANNING_WORKFLOW.md) |
| 城市状态、事实、导出删除 | [CITY_STORAGE_AND_FACTS](CITY_STORAGE_AND_FACTS.md)、[M7_MEMORY](M7_MEMORY.md) |
| 助手与模型预算 | [CITY_AGENT_V3](CITY_AGENT_V3.md)、[V3.4调度验收](AGENT_V3_RATE_LIMIT_AND_LIVE_ACCEPTANCE.md)、[KIMI_API](KIMI_API.md) |
| 地图、参考查询与主版本接入 | [AMAP_MAP_LOCATION_MVP](AMAP_MAP_LOCATION_MVP.md)、[AMAP_REAL_API_CAPABILITY_REPORT](AMAP_REAL_API_CAPABILITY_REPORT.md)、[MAIN_REFERENCE_MERGE_ACCEPTANCE](MAIN_REFERENCE_MERGE_ACCEPTANCE.md) |
| 数据建设与质量 | [DATA_REQUIREMENTS](DATA_REQUIREMENTS.md)、[DATA_SOURCES](DATA_SOURCES.md)、[DATA_ENGINEERING_PLAN](DATA_ENGINEERING_PLAN.md)、[DATA_QUALITY_REPORT_SPEC](DATA_QUALITY_REPORT_SPEC.md) |
| 语音与依赖许可 | [SPEECH](SPEECH.md)、[THIRD_PARTY](THIRD_PARTY.md) |
| 未闭环问题的诊断 | [AGENT_KNOWLEDGE_QA_DIAGNOSIS](AGENT_KNOWLEDGE_QA_DIAGNOSIS.md)；这是诊断证据，不是修复验收 |

## 设计来源与证据

- [AI_ARCHITECTURE](AI_ARCHITECTURE.md) 为2026-10-20目标设计，不能当作当前实现清单；[reference/README](reference/README.md)说明原始PDF、Word及任务来源。
- [reports/](reports/) 保存各轮机器回执、诊断和交付记录；[screenshots/](screenshots/)保存相应截图。失败和中断回执保留，测试数不得跨轮相加。
- [archive/README](archive/README.md)集中早期单景区测试、城市原语/存储、V2/V3.3和前端阶段记录。
- 本次改动清单与验证结果见[文档清理记录](reports/documentation-cleanup-20261023.md)。

现状页只保留结论和证据链接，专项文档负责具体契约。功能合并、事实导入或验收变化后同步更新，避免各文档各写一套“最新完成度”。
