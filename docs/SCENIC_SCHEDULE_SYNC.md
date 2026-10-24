# 景区演出日程同步（清明上河园 / 万岁山）

2026-10-24 首次实现。本模块是独立的 PostgreSQL 日程域，与现有 SQLite 城市事实库、OR-Tools 规划引擎和预览/确认机制分离；采集结果只有在管理员审核并显式发布后才可能成为求解候选。

## 架构边界

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 解析/Provider | `backend/app/scenic_sync/parse.py`、`provider.py` | 只读官方公开 HTML 表格；禁止绕过防护、私有 API、逆向小程序；403/429 即停止 |
| 人工导入 | `manual_import.py` | 万岁山官方小程序截图/PDF 的人工录入，SHA-256 哈希校验 |
| 持久化 | `migrations/001_scenic_schedule_domain.sql` | 采集版本、来源、内容哈希、变更 diff、场地绑定、管理员回执 |
| 查询 API | `api.py`、`service.py` | 游客只读三个接口；管理员接口需 `X-Scenic-Admin-Key` |
| 显式发布 | `publish.py`、`mapper.py` | 时长/入场规则/场地绑定/有效有向路由齐全才允许写入城市事实；取消只产生重规划信号 |
| 前端 | `frontend/src/components/ScenicSchedule.vue` | 按日期查询，区分官方已核验/旧日程/今日未确认/临时变动 |

## 已有能力 vs 本次真正补齐

已有：日期绑定场次 `city_facts`、取消不自动改写行程、预览/确认、有向园内路网（不自动补逆向边）、来源/有效区间追踪。本次补齐：采集版本化与审计、同日 diff、403/429 熔断、断崖下降隔离、人工证据哈希、场地绑定、管理员审核、游客查询接口和前端状态区分。

## 部署步骤

1. 提供独立 PostgreSQL 实例，配置 `SCENIC_SCHEDULE_DATABASE_URL`（见 `.env.example`）。
2. 执行迁移：`PYTHONPATH=backend .venv/bin/python -c "import os;from app.scenic_sync.store import migrate;print(migrate(os.environ['SCENIC_SCHEDULE_DATABASE_URL']))"`
3. 配置 `SCENIC_ADMIN_API_KEY`（32 位以上）和 `SCENIC_EVIDENCE_ROOT`（受保护附件目录）。
4. 确认清明上河园站点条款、robots 与授权后再设置 `SCENIC_QMSY_SYNC_ENABLED=1` 和 `SCENIC_SOURCE_POLICY_REVIEWED=1`；此前该开关默认关闭，定时任务不应启用。
5. 定时任务建议：北京时间 08:00 一次，开园期间每 60–120 分钟一次（`SCENIC_MIN_INTERVAL_MINUTES` 默认 60）。仅请求一次公开 HTML 页面，绝不自动重试或切换 IP。

## 定时任务示例（仅在授权确认后启用）

```bash
# crontab -e（每行代表一次独立进程，进程间无共享状态，数据库按 park+date 去重）
0 8 * * * cd /path/to/kaifeng-tourism-mvp && PYTHONPATH=backend .venv/bin/python scripts/scenic_sync_cron.py qmsy >> /var/log/scenic_sync.log 2>&1
*/90 9-21 * * * cd /path/to/kaifeng-tourism-mvp && PYTHONPATH=backend .venv/bin/python scripts/scenic_sync_cron.py qmsy >> /var/log/scenic_sync.log 2>&1
```

## 人工导入万岁山

1. 队友从官方小程序获取当天节目单截图/PDF，放入 `SCENIC_EVIDENCE_ROOT` 目录。
2. 按 `backend/tests/fixtures/scenic_sync/wansui-template.csv` 格式填写，每批只允许一个服务日期和一个证据文件。
3. 通过管理员接口上传：`POST /api/admin/scenic/parks/wansui/imports`（multipart：`file` CSV、`evidence_file` 附件名、`request_id` 幂等键），需 `X-Scenic-Admin-Key` 与 `X-Scenic-Reviewer`。
4. 结果需管理员二次审核：`POST /api/admin/scenic/parks/wansui/schedules/{schedule_id}/review`，确认 `applicability_verified=true` 与 `completeness_verified=true`。

## 回滚

- 新增表均为 `scenic_*` 前缀，不触碰现有城市表、规划表、M7 记忆或旧单景区数据。
- 回滚顺序：先移除前端 `ScenicSchedule` 引用与 API 路由注册，再删除 `backend/app/scenic_sync/`、`backend/tests/test_scenic_sync*.py`，最后在 PostgreSQL 中 `DROP SCHEMA public CASCADE` 或只删除 `scenic_*` 表；SQLite 城市库中已发布的 `scenic-schedule-source-*`、`scenic-program-*`、`scenic-*-session` 记录需用 `import_city_facts.py` 撤销机制处理，不属于本模块自动清理范围。

## 未完成项

- 万岁山官方小程序自动同步：尚无正式合作 API 证据，当前 `source_api_status=not_verified`，只有人工审核通道。
- 节目持续时长、入场截止、预约/天气敏感性、场地坐标、园内真实有向路网：未提供时保持 null/unknown，不进入求解器。
- 前端演出介绍与当日节目表并存；介绍内容仍来自静态工作簿，标注待复核。
