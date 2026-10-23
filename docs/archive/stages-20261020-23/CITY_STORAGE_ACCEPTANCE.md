# 城市安排与事实入口验收

> 历史阶段记录：仅描述原日期的软件、操作与验收边界，不作为当前开发待办或最新完成声明。当前状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。

日期：2026-10-21。本文只记录本轮软件实现和数据边界，不把合成 fixture 当作开封真实资料，也不把通过软件测试当作现场导航验收。

## 交付结果

- M7 城市行程扩展：服务端保存日期、开始/结束、起终点、兴趣、菜品目标、活动、状态、完成/跳过前缀和用户手动当前位置；刷新、记忆页、导出读取同一权威版本。
- 约束门禁：属主、CSRF、请求幂等、版本 CAS、重要修改候选与影响说明、事实修订摘要、15 分钟候选时效、事务回滚、72 小时终态留存、删除台账恢复门禁。
- 事实入口：地点、来源、入口、营业规则、节目身份、日期场次、菜品、商家、供应关系；版本化当前值/不可变修订，整批关联校验，公开许可过滤和 `ready/issues` 缺口。
- 前端：新增 `/city-trip`，从助手和记忆页进入；空事实库仍可保存部分时间需求，页面明确“尚未计算城市路线”。重要删除/时间调整先展示受影响必选活动，确认前不写版本。
- 运行库：只做增量建表和 `active_city_trip_id` 字段；城市事实表为空，未导入 14 条测试 fixture 或任何虚构开封数据。

## 实际验证

| 检查 | 命令/证据 | 结果 |
| --- | --- | --- |
| 后端 | `./scripts/check.sh` | 212 passed；前端 `vue-tsc` 和 Vite production build 通过；仅有 Starlette/httpx deprecation warning |
| 浏览器 | 下方完整命令，独立临时库 | 39 passed；覆盖空事实保存刷新、保存/导出、确认影响、版本冲突、跨标签页删除、320px 完成/手动锚点以及原有页面回归 |
| 迁移 | [city-migration-check-20261021.json](../../reports/city-migration-check-20261021.json) | 从迁移前备份副本建立新表；旧表逐列逐行一致；运行库旧数据一致；`foreign_key_check=[]`、`integrity_check=ok`；删除台账未替换 |
| 实际 HTTP | [city-runtime-smoke-20261021.json](../../reports/city-runtime-smoke-20261021.json) | `/api/health` 200；能力接口明确 `city_planning_available=false`；事实返回空 `items`；`/city-trip` 返回 SPA |
| 可视化 | [city-state-mobile.png](../../screenshots/city-state-mobile.png) | 320px 合成事实测试截图已人工检查；不代表真实地点或真实路线 |

复现浏览器验证（使用本机已安装 Chromium；fixture 只由测试启动器导入临时库）：

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp/frontend
E2E_CITY_FACTS=/Users/mac/Desktop/大挑/kaifeng-tourism-mvp/backend/tests/fixtures/city_facts.json \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Users/mac/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' \
npm run test:e2e
```

## 文件范围

主要新增：`backend/app/city/facts.py`、`storage.py`、`state_schema.py`、`state.py`、`frontend/src/cityApi.ts`、`frontend/src/pages/CityTrip.vue`、`scripts/import_city_facts.py`、`data/templates/city-facts.template.json`、`docs/CITY_STORAGE_AND_FACTS.md`。

主要修改：`backend/app/main.py`、`backend/app/memory/{storage,session,api}.py`、`backend/app/city/api.py`、`frontend/src/{api.ts,memoryTypes.ts,main.ts}`、`frontend/src/pages/{Assistant,Memory}.vue`、`scripts/restore_memory_backup.py`、`scripts/browser_server.py`、README/约束/数据/M7/架构文档及事实 schema。

测试 fixture 与新增后端/浏览器测试只用于临时数据库，见 `backend/tests/fixtures/city_facts.json` 和 `backend/tests/test_city_state.py`；没有把 fixture 当成运行数据。

## 仍需真实数据或后续实现

当前仍缺开封真实且有来源的地点/入口/道路通行、目标日期演出场次与结束/入场规则、餐饮营业和供应关系、再入园规则、返程点及城市转场估时。高德后端 provider 尚未接入，现有前端高德跳转不提供后端矩阵；NetworkX/OR-Tools 仍是单景区能力，尚未适配城市活动实例和联合预算。客流没有真实连续样本，不能开启可靠预测。

因此当前交付是“可保存、可恢复、可核验导入、可记录途中进度”的城市状态基础，不是城市导航 V1。下一阶段先导入一条现场核验的有限链，再接转场估时、联合求解、独立全程复核和助手/地图共同重规划。
