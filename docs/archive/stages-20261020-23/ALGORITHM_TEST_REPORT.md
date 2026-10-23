# 算法集成验证记录

> 历史阶段记录：仅描述原日期的软件、操作与验收边界，不作为当前开发待办或最新完成声明。当前状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。


> 适用范围说明：本报告验证的是 2026-10-20 前的单景区代码与测试夹具。随着城市级 V2 方向确定，它保留作为历史软件证据，不代表城市跨场景、真实餐饮、城市交通或新 V1 验收已通过。当前验收以 [DEVELOPMENT_DIRECTION.md](../../DEVELOPMENT_DIRECTION.md) 为准。

日期：2026-10-20。以下结果验证软件行为，测试场次、道路变更和客流夹具不属于景区真实资料。没有对真实游客效果、景区通行能力或真实 Provider 质量作出结论。

## 最终结果

| 验证 | 实际命令 | 结果 |
| --- | --- | --- |
| 后端回归与新增约束 | `./scripts/check.sh` 内的 `.venv/bin/pytest -q` | 96 passed，1.85秒 |
| TypeScript 与生产构建 | `./scripts/check.sh` 内的 `npm run build --prefix frontend` | vue-tsc 与 Vite 成功 |
| 全部浏览器用例 | 见下方命令 | 14 passed，15.2秒 |
| Python 依赖一致性 | `.venv/bin/python -m pip check` | No broken requirements found |
| 原有数据完整性 | 对备份与现库四表按 rowid 读取并比对 SHA-256 | scenic 5、networks 2、observations 504、plans 4；四表行内容全部相同 |
| AI 原始文档归档 | 对 Downloads 原件与 docs/reference 副本计算 SHA-256 | 完全一致 |
| 手机界面 | Playwright 320/390px 无横向溢出；查看算法结果页截图 | 通过 |

浏览器实际使用已有兼容 Chromium，无新增浏览器下载：

```bash
cd frontend
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Users/mac/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' npm run test:e2e
```

新环境按 README 安装 Playwright 对应 Chromium，或通过同一个环境变量指定兼容路径。浏览器服务只绑定127.0.0.1，全部自动化使用临时 SQLite；不写用户开发库。后端有一个既存 Starlette TestClient 使用 httpx 的弃用提示，不影响本轮断言。

## 覆盖内容

- 原有景区查询、NetworkX 最短路、兴趣差异、时长预算、必去点、结果持久化、CSV 事务与幂等、客流来源优先级，以及独立语音功能继续通过。
- 新增46个后端测试案例：固定演出开始、候场、完整观看与出园预算；多场次选择、必看冲突；景区/点位开放区间；过期、未核验和模拟日程隔离；日程导入整批校验；静态封路、单行、不可达、缺路网与弯曲道路几何；未知客流、不同时日观测、过去目标防泄漏；估计器故障、无效值与混入模拟数据的降级；求解器故障与独立校验；语言服务无配置、错误结构、名称映射和受约束解释。
- 新增2个浏览器流程：精确日期/分钟进入实际求解与刷新；模拟语言响应填表、人工确认后调用真实算法，语言服务失败仍可使用表单。Provider 响应为测试替身，不是实际模型效果验证。
- 浏览器同时通过：首页图片、独立滑动、客流缺失状态、地图交互、修改策略重新规划、320px布局、录音/转写模拟交互与关闭资源释放。

## 发现并修复

首次测试暴露有向图连通性检查不适配、路线几何格式兼容、兴趣奖励与时间成本比例、旧小时请求遗漏当前观测。随后修复并通过回归。当前图采用强连通性检查；路线几何沿真实已存边拼接，保留弯折；小时级旧接口保持兼容，精确日期时间接口避免使用目标时刻之后的数据。

浏览器测试发现生产静态图片未单独托管、首页滑动轨道越界4px、新提示复用了旧测试的单元素选择器。分别修复静态 `/images`、轨道右边距和提示样式名。首页图片回归最后将同名标题检查收窄到 h1，避免将地图内 h3 当页面主标题；没有删除断言或降低产品验收条件。

测试开始受本机沙箱端口绑定限制，使用允许本机监听的执行环境后通过。默认 Playwright 浏览器版本缺失，改用已有兼容 Chromium 后通过；这些初始环境失败没有记为产品通过。

## 数据和外部核查边界

- 真实路网：未取得，仍为原来的两套示意坐标。没有导入虚构道路。
- 真实演出：未取得。提供日期有效且带来源的导入结构、读取API与求解能力；在合成夹具下验证时间窗。游客无日程时只安排场地游览。
- 真实客流：运行库无非模拟观测；历史统计及插件结构已实现，不训练模型、不报告准确率。当前历史方法未区分节假日和天气。
- 拥挤优化：当前为入园时段快照，未实现逐段到达时刻的动态客流更新。部分未知使用明确标注的策略成本，全部未知则关闭客流目标。
- 自然语言：接入结构、服务协议、校验、前端填表和模板降级已实现；没有真实API Key调用，真实模型质量未验证。
- OSM：主端点返回 HTTP 错误、备用端点 ReadTimeout。保留探针与错误记录，不把“查询失败”解释成“景区没有OSM道路”。
- HTS-RPR：读到仓库设计与实验说明；未获取论文全文，未确认具体 LICENSE，未复用其代码或数据，也未复现其效果。
- 部署：生产构建和临时本机真实HTTP闭环通过；未做公网部署，未改已有服务进程或任何付费服务配置。正常使用仍运行项目根目录 `./start.sh`。

## 主要交付与文件

详细模型、限制、模块数据流、API 和研究对比建议见 [ALGORITHMS.md](../../ALGORITHMS.md)。版本、许可及版权通知见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。截图在 `docs/screenshots/algorithm-result-mobile.png`。

新增核心文件：`backend/app/paths.py`、`itinerary.py`、`schedules.py`、`crowd_provider.py`、`language.py`；维护脚本 `scripts/import_schedules.py`、`audit_osm.py`；后端和前端算法测试、执行说明、原始设计副本、依赖许可证与OSM核查记录。

修改核心文件：`backend/app/planner.py`、`models.py`、`crowds.py`、`db.py`、`main.py`、两份依赖清单；前端 `api.ts`、`types.ts`、`Planner.vue`、`Result.vue`、`style.css`；`test_mvp.py`、`home-display.spec.ts`（仅准确限定标题）；`.env.example`、`AGENTS.md`、`README.md`。

原实现和数据库快照保留于 `.backups/algorithms-20261020/`；当前新增表为幂等增量结构，无破坏性数据库变更。
