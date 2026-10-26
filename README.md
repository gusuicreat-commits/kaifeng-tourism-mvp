# 汴梁游记

面向开封自由行的城市旅游助手，以景区、演出、美食、休息与交通组成的连续行程为核心，支持途中修改。Vue 3 + TypeScript / FastAPI，城市行程使用 SQLite，独立演出日程使用 PostgreSQL；使用 NetworkX 与 OR-Tools CP-SAT 规划和独立复核。

**截至2026-10-25：这是可运行的城市旅游原型。规划引擎、素材篮、助手、行程确认和地图已有软件接线，参考资料和部分外部服务已有接入记录；目标日完整演出、园内真实路网及完整 V1 现场验收仍未完成。** 干净安装不会复制本机运行数据库，参考资料、合成测试与已确认路线须区分。第三方审阅建议先看[提交前检查与剩余限制](docs/reports/github-readiness-20261025.md)。

## 团队接手

Windows首次运行：`py -3 scripts/start.py`，或双击 `start.cmd`。完整步骤、密钥配置、数据缺口和验收边界见 [HANDOFF.md](HANDOFF.md)。安装脚本同时安装基础和采集/工作簿依赖。

## 开发入口

- [项目详细说明](docs/PROJECT_GUIDE.md)：技术栈、功能亮点、架构、目录与完整运行指南。
- [文档导航](docs/README.md)：现行规范、专项说明和历史证据。
- [项目现状与缺口](docs/PROJECT_STATUS.md)：代码、运行库和最近验收记录的核对结果。
- [产品需求](docs/PRD.md)、[开发方向](docs/DEVELOPMENT_DIRECTION.md)、[项目约束](AGENTS.md)：范围与完成判据。
- [当前工程架构](docs/CITY_ARCHITECTURE.md)：实际模块及连接；[目标架构](docs/AI_ARCHITECTURE.md)供追溯设计，不当作完成声明。

## 当前入口与边界

| 页面 | 当前用途 |
| --- | --- |
| `/`、`/scenic/:id`、`/food/:id`、`/place/:id`、`/vendor/:id` | 浏览景点与餐饮素材，详情查询、加入素材篮；缺真实资料显示待添加 |
| `/basket` | 同一旅程的服务端素材篮、优先级与规划入口 |
| `/assistant` | 收集条件、只读问答、参考问路与有限工具探索；路线变更需经确认门禁 |
| `/city-trip/:id?` | 查看正式行程、下一站、完成进度与调整；没有可用路线时显示空态 |
| `/city-trip/manage/:id?` | 结构化城市草稿管理 |
| `/guide` | 真实地图、临时选点与参考路线；`?demo=1` 是显式示意入口 |
| `/me`、`/memory` | 当前旅行、会话私有资料的导出与删除；没有账号及跨设备身份恢复 |
| `/pitch` | 独立路演页面；其中的交互示例不写入游客真实行程 |

素材篮是意向，预览尚未提交；正式行程来自同一引擎确认接口。地图路线查询不会自动成为正式交通矩阵，也不证明园内可通行。旧单景区接口继续兼容，模拟客流与示意道路不作为真实观测或导航依据。

## 启动

需要 Python 3.11+、Node.js 22.12+及 npm；首次安装需要联网。

```bash
cd kaifeng-tourism-mvp
./start.sh
# 开发热更新
./start.sh --dev
```

默认应用/API 为 `http://127.0.0.1:8017`，开发前端为 `http://127.0.0.1:5177`，API 文档为 `/docs`，健康检查为 `/api/health`。端口以本地配置为准。默认模式提供已构建前端；修改后需重建，后端修改需正常重启。Ctrl+C 停止本启动器的子进程。

修改后运行 `.venv/bin/python scripts/runtime_status.py`（Windows：`.venv\Scripts\python scripts/runtime_status.py`），核对8017实际加载的后端与本目录。`current`才表示启动指纹与磁盘一致；`restart_required`说明代码、提交或目录不一致，`unknown`说明旧服务缺少启动标识，`unavailable`说明服务不可达。检查只读，不会杀进程或修改数据。开发时可使用上面的`--dev`自动重载；它不代替保存改动后的测试与指纹核对。

## 配置与数据维护

首次从 `.env.example` 复制到 `.env`；已有配置只补字段，不覆盖。配置名称以 `.env.example` 为准，密钥只写本地 `.env`。

- 地图使用 `AMAP_JS_KEY` / `AMAP_JS_SECURITY_CODE` 和独立的 `AMAP_WEB_SERVICE_KEY`；安全码经后端代理，不使用旧 `VITE_AMAP_SECURITY_CODE`。见[地图说明](docs/AMAP_MAP_LOCATION_MVP.md)。
- 地点搜索与参考问路由 `AMAP_LOCATION_SEARCH_ENABLED`、`AMAP_TERMS_REVIEWED`、`AMAP_LOCATION_POI_API_VERSION`、`AMAP_REFERENCE_ROUTES_ENABLED` 控制。见[主版本合并验收](docs/MAIN_REFERENCE_MERGE_ACCEPTANCE.md)。
- Qwen / Kimi 使用 `TOURISM_LLM_BASE_URL`、`TOURISM_LLM_MODEL`、`TOURISM_LLM_API_KEY`，本机已切换 Qwen3.7-Plus，见[模型接入说明](docs/KIMI_API.md)。
- 百炼语音使用 `DASHSCOPE_API_KEY`，仅转写到输入草稿，不自动发送或修改行程。见[语音说明](docs/SPEECH.md)。手机录音和定位需要合适的 HTTPS 环境。

城市事实只经 `scripts/import_city_facts.py` 校验及导入，默认 dry-run，显式 `--apply` 才写入；交通运营资料经 `scripts/import_city_operations.py`。格式和核验要求见[存储与事实](docs/CITY_STORAGE_AND_FACTS.md)、[数据需求](docs/DATA_REQUIREMENTS.md)和[数据工程计划](docs/DATA_ENGINEERING_PLAN.md)。采集台账与运行事实库分开，fixture 不得导入运行库。恢复备份须使用当前删除台账，见[M7](docs/M7_MEMORY.md)。

演出日程需要另行配置 PostgreSQL 并执行[日程迁移](docs/SCENIC_SCHEDULE_SYNC.md)，未配置时页面显示尚未确认。公共客流 CSV 导入与景区节目管理均需服务端 `SCENIC_ADMIN_API_KEY` 和审核人请求头，见[安全说明](SECURITY.md)。

## 验证

```bash
./scripts/check.sh
# 后端、类型检查和前端构建
cd frontend
npm run test:e2e
```

浏览器验证使用隔离临时库，默认配置只向这个临时库装入合成测试资料；`E2E_EMPTY_CATALOG=1` 可验证空目录场景。不会写入本机旅游库。PostgreSQL 用例仅在配置独立的 `SCENIC_TEST_DATABASE_URL` 后执行，这些测试会重建该测试库的 public schema，禁止使用业务库连接。采集器测试为 `.venv/bin/python -m pytest -q collector/tests`。专项命令见[规划引擎](docs/PLANNING_ENGINE_V1.md)、[V3](docs/CITY_AGENT_V3.md)及[地图说明](docs/AMAP_MAP_LOCATION_MVP.md)。在线测试可能产生 API 调用，应显式启用。

`npm run build` 会从 `frontend/pitch/` 自动生成路演；生成的 HTML 不重复提交。历史采集参考摘录已保存在 `data/mvp/source/historical-catalog-20261020.json`，保留来源摘要及未核验状态，重新整理资料不再依赖作者电脑上的相邻目录。摘录不是原始网页证据。

最近已有测试结果、失败项和未覆盖范围统一见[项目现状](docs/PROJECT_STATUS.md)。历史测试报告移至[归档](docs/archive/README.md)，不替代当前回归或实地验收。
