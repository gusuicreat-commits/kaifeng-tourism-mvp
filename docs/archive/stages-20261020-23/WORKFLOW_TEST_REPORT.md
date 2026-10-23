# 小汴梁问答改造验证记录

> 历史阶段记录：仅描述原日期的软件、操作与验收边界，不作为当前开发待办或最新完成声明。当前状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。


> 适用范围说明：本报告验证的是旧单景区问答 Workflow 与既有 API 集成。它可以证明旧链路的回归结果，不能证明新的 CityTrip、城市转场、真实餐饮接续或途中跨场景重规划；当前新范围以 [DEVELOPMENT_DIRECTION.md](../../DEVELOPMENT_DIRECTION.md) 为准。

日期：2026-10-20。范围为需求收集 Workflow、统一助手入口、导览跳转和结果页修改需求；复用既有 GIS/行程计算。项目当前没有 Git 仓库，本次修改前源码归档在 `.backups/guided-planning-20261020/source-before.tar.gz`。

## 验证结果

| 检查 | 实际结果 |
| --- | --- |
| `./scripts/check.sh` | **96 项后端测试通过**，TypeScript 检查及 Vite 生产构建通过。 |
| `npm run test:e2e`（使用下方已安装的 Chromium） | **27 项通过**：22 项浏览器用例及5项 Workflow 状态测试。 |
| 最终确认文案调整后 | TypeScript / Vite 再构建通过，`tests/interview.spec.ts` **5 项复测通过**。本机8017入口返回最新构建。 |
| 可视检查 | 已查看1440px桌面开场、390px手机开场及手机确认页截图；布局、按钮和需求卡可读，320px无横向溢出。 |

浏览器运行命令：

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp/frontend
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Users/mac/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' npm run test:e2e
```

测试服务器使用 `scripts/browser_server.py` 创建独立临时 SQLite 数据库，并在结束后清理。规划用例提交到实际 FastAPI / NetworkX / OR-Tools；自然语言行为用例模拟解析响应，不调用付费模型。场次变化测试夹具只在浏览器请求拦截中使用，不写入开发数据库。

验证覆盖：

- 必填日期、时刻、时长没有隐式默认值，缺项不能进入计算，只追问缺失字段。
- 兴趣和节奏允许显式跳过；核对确认前不会提交规划；同一标签页刷新保留需求。
- 自然语言明确字段可补全多步；未匹配必去点必须核对；服务失败仍能用选项完成真实规划。
- 切换景区清除旧必去点/场次、保留其他需求；修改日期清除旧必看场次。
- 原请求的必去点、具体分钟、总时长进入实际求解；无已核验演出时只安排场地游览。
- 时间不够时展示业务错误，修改后重新求解；缺路网阻止提交并支持重新加载；日程失败不虚构演出；客流缺失/失败仍可普通规划。
- 游客请求固定使用非模拟数据，未知客流不显示为低拥挤；结果刷新、策略调整及带原需求返回问答可用。
- 导览进入同一问答并带景区；旧规划/客流链接兼容；三项手机主导航、首页展示和已有语音工具回归通过。

第一轮浏览器验证发现导览浮动入口的持续位移动画妨碍稳定点击，已移除该动画；修复后导览跳转通过。后端测试有1条既有 Starlette/httpx 弃用提示，不影响通过结果。

## 代码交付清单

新增：

- `frontend/src/planningWorkflow.ts`：结构化草稿、问题、缺项与流转、恢复及请求组装。
- `frontend/src/components/GuidedPlanner.vue`：问答交互、共享需求卡、现有 API 集成。
- `frontend/tests/interview-helpers.ts`、`interview.spec.ts`、`workflow-state.spec.ts`：闭环辅助与状态/异常测试。
- `docs/PLANNING_WORKFLOW.md`、本记录及 `docs/screenshots/workflow-*.png`。

修改：

- `frontend/src/pages/Assistant.vue`：替换固定模拟聊天。
- `frontend/src/pages/Guide.vue`、`Result.vue`：统一问答跳转及带回原需求。
- `frontend/src/App.vue`、`main.ts`：合并导航和旧链接兼容。
- `frontend/src/api.ts`：自然语言请求支持取消。
- `frontend/src/style.css`：问答适配及删除旧弹层专用样式。
- `frontend/tests/algorithms.spec.ts`、`flows.spec.ts`、`guide.spec.ts`：保留原计算/展示验收并改走新入口。
- `backend/app/language.py`：故障提示由“表单”调整为“选项回答”，计算逻辑未改。
- `AGENTS.md`、`docs/PRD.md`、`README.md`：记录已确认产品流程与入口。

删除：

- `frontend/src/pages/Planner.vue`：重复手工规划页面。
- `frontend/src/components/GuideAssistant.vue`：独立模拟聊天。

## 尚未验证的边界

本次证明软件问答、状态管理、接口和既有求解的连通性；未证明真实 Provider 的语义理解质量，未完成真实景区路网、实时客流、官方当日演出及现场导航验收。当前示意路网保留明确提示，软件测试不能替代现场数据核验。
