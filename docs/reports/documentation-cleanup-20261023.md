# 文档核对与清理记录

日期：2026-10-23。范围为主目录 `kaifeng-tourism-mvp` 的开发文档及项目约束；未清理其他隔离目录、运行数据或采集原件。本轮只读核对源码、SQLite运行库、Git状态与既有验收回执，没有重跑业务测试或外部API。

## 核对结论

| 过时内容 | 本次处理 |
| --- | --- |
| README只介绍旧单景区、把美食限为选中反馈、城市规划列为未接通 | 按实际路由和服务端素材篮/工作区/引擎重写，说明意向、参考路线、预览与正式确认的区别 |
| 工程架构仍把地图和城市助手画成待接线 | 重绘当前连接，列出代码入口与真实数据门槛 |
| 算法文档把活动实例、时间窗、联合求解和剩余重规划全部列待开发 | 区分旧单景区限制与已实现城市引擎；保留尚未通过的正式交通和现场门槛 |
| M7、存储文档将route_version永远写为0 | 改为未确认时0、确认后递增；旧单景区拒绝逻辑限旧接口 |
| 开发方向仍沿用旧待办表 | 保留来源、决策与V01–V18；按真实剩余缺口更新实施表 |
| Kimi、高德等阶段报告的当轮未接入描述容易当成现状 | 添加状态入口和时点说明；真实调用、合成业务、设备现场验收分别表述 |
| 数据工程计划仍像全部尚未开工 | 标明已有collector/台账与实际模块命名，阶段完成仍须独立验收；不改并行采集实现 |
| 多份文档各自宣称“当前” | 新增统一[现状页](../PROJECT_STATUS.md)与[文档导航](../README.md)，项目AGENTS加入维护入口 |

## 清理清单

删除 **1份**：`docs/DETAIL_BASKET_IMPLEMENTATION.md`。这是实施前缺项清单，仍把已实现的服务端素材篮等写成缺失；有效数据和确认边界已纳入现状、架构与交互规范。原件在本地清理备份中可恢复。

归档 **10份**：

- [TEST_REPORT.md](../archive/stages-20261020-23/TEST_REPORT.md)
- [WORKFLOW_TEST_REPORT.md](../archive/stages-20261020-23/WORKFLOW_TEST_REPORT.md)
- [ALGORITHM_TEST_REPORT.md](../archive/stages-20261020-23/ALGORITHM_TEST_REPORT.md)
- [M7_ACCEPTANCE.md](../archive/stages-20261020-23/M7_ACCEPTANCE.md)
- [CITY_ARCHITECTURE_ACCEPTANCE.md](../archive/stages-20261020-23/CITY_ARCHITECTURE_ACCEPTANCE.md)
- [CITY_STORAGE_ACCEPTANCE.md](../archive/stages-20261020-23/CITY_STORAGE_ACCEPTANCE.md)
- [CITY_ASSISTANT_V2.md](../archive/stages-20261020-23/CITY_ASSISTANT_V2.md)
- [AGENT_V3_LIVE_ACCEPTANCE.md](../archive/stages-20261020-23/AGENT_V3_LIVE_ACCEPTANCE.md)
- [FRONTEND_REDESIGN.md](../archive/stages-20261020-23/FRONTEND_REDESIGN.md)
- [FRONTEND_TASK_SIMPLIFICATION.md](../archive/stages-20261020-23/FRONTEND_TASK_SIMPLIFICATION.md)

这些文件有独立的测试、迁移、限流失败或产品迭代依据，移出主阅读路径并标明历史范围，不作为无价值文件直接抹掉。归档内相对链接按新位置重写，所有引用方同步更新。

保留原位：原始PDF/Word、目标设计、机器JSON回执、截图、契约、许可证、诊断及研究证据。最新10月24日草案有已知数值和结论问题，现状页明确记录；它是输出缺陷证据，本轮没有改写或删除历史产物。

## 仍未解决的两项

1. `backend/app/city/api.py` 的capabilities仍列 `city_assistant_frontend` 未接通。当前文档明确指出接口落后于接线实现，本轮不改业务接口或测试。
2. Git已有仓库且分支为main，但提交数为0、项目文件未跟踪。本轮有独立文档备份和变更清单，没有整仓暂存、首次提交或覆盖其他工作的文件。

## 验证与恢复

- 对现行文档、归档及报告检查Markdown相对文件链接；最终数量和零断链结果见[机器清单](documentation-cleanup-20261023.json)。
- 对比清理前指纹：后端、前端源码及scripts未改动；原始需求、机器回执、截图、契约、许可证和研究JSON保持原字节。
- 核对运行库只读计数：city_facts、city_fact_revisions、schedule_facts、city_engine_operations、city_engine_routes均为0，observations=504、networks=2。没有导入或删除业务数据。
- collector若在本轮期间发生变化，记录为并行编辑观察；本轮编辑脚本仅操作README、AGENTS及docs，不写collector，也不恢复其旧副本。
- 文档变动不运行整套业务测试；既有625项后端及浏览器84/4/1只作为原轮次证据，不标成此次成绩。

清理前备份位于项目 `.backups/docs-cleanup-20261023/documents-before.tar.gz`，同目录含 `before.json` 和核对记录。需要恢复时只提取清单中的目标文档，先检查之后的新编辑；不整包覆盖当前项目，不覆盖数据库、配置或删除台账。
