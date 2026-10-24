# 城市安排存储与事实入口

存储契约基线为2026-10-21，入口与依赖于2026-10-23复核。总范围见 [PRD](PRD.md)，存储阶段证据见 [CITY_STORAGE_ACCEPTANCE.md](archive/stages-20261020-23/CITY_STORAGE_ACCEPTANCE.md)，当前完成度见 [PROJECT_STATUS](PROJECT_STATUS.md)。

## 可以使用的功能

游客从“行程”进入 `/city-trip` 查看旅行；结构化管理入口为 `/city-trip/manage/:id?`，也可从记忆管理打开已保存安排。同一浏览器会话内刷新恢复服务端当前版本，日期、起终点、时间、兴趣、具体活动、餐饮目标和手动进度保持同一城市行程 ID。未录入真实地点时，可以先保存日期和起止时间；缺项明确提示。

此处保存的是**需求与用户自报进度**，草稿本身不提供路线可行性承诺。未确认引擎路线时 `route_version=0`；经独立校验和用户确认后路线版本递增。城市联合求解、剩余重规划及地图/助手共享状态已有独立接线，详见 [规划引擎](PLANNING_ENGINE_V1.md)。正式交通准入与真实资料仍待验收。旧单景区问答和路线不被自动迁移或覆盖。

## 数据流与模块

```mermaid
flowchart LR
  Evidence[原始证据与人工核验] --> CLI[维护 CLI：格式/关联/版本校验]
  CLI --> Catalog[city_facts 当前值与不可变修订]
  Catalog --> Query[公开许可事实查询与缺项状态]
  Query --> UI[城市安排编辑器]
  UI --> Gate[M7 会话 / CSRF / 属主 / 幂等 / 版本]
  Gate --> Preview[重要修改：预览影响并确认]
  Preview --> State[城市需求与进度事务保存]
  Gate --> State
  State --> Memory[恢复 / 导出 / 删除与留存]
```

事实由维护入口管理，游客不能声明事实已经核验。LLM 不参与当前城市状态写入；后续自然语言只能生成类型化候选，复用确认门禁。前端不能填写属主、事实版本或路线版本。

| 实现 | 职责 |
| --- | --- |
| `backend/app/city/facts.py` | 事实契约、关联校验、版本导入、依赖与日期有效性、公开许可过滤 |
| `city/storage.py` | 增量表、会话指针、城市对象留存 |
| `city/state_schema.py`、`state.py` | 草稿、手动进度、位置锚点、候选、确认及原子提交 |
| `city/api.py` | 只读事实查询与会话私有城市接口 |
| `frontend/src/cityApi.ts`、`pages/CityTrip.vue` | 编辑、冲突提示、确认、恢复、手动记录 |
| `memory/`、`pages/Memory.vue` | 共用身份、台账、删除、导出及记录入口 |

## 事实数据格式

空模板：[city-facts.template.json](../data/templates/city-facts.template.json)。完整字段及约束以 [JSON Schema](contracts/city-facts-v1.schema.json) 为准。模板没有示例道路、店名或演出时间；测试 fixture 仅在临时库使用，不能作为真实数据包。

| `kind` | 主字段与关系 |
| --- | --- |
| `source` | `fact_id`、名称、来源类型、发布者、URL 或文件页码等 `locator`、采集时刻、可选发布时间 |
| `place` | 名称、类型、可选上级地点、地址、成对经纬度和明确坐标系 |
| `entrance` | 所属地点、入口地点、入/出方向；入口端必须是 entrance 类型 |
| `open_rule` | 地点、业务日期、营业区间、停止入场、再入园、预约和票种范围 |
| `program` | 节目身份和名称，不替代独立日期场次 |
| `show_session` | 节目、独立舞台、业务日期、开始、可选结束及停止入场时刻 |
| `food` | 菜品身份和名称，不能直接作为餐饮停留地点 |
| `vendor` | 商家身份，关联 restaurant 类型地点 |
| `supply` | 商家、菜品、支持具体供应关系的证据说明及来源 |

所有记录使用稳定 `fact_id`、从 1 开始的 `version`，记录 `source_ids`、`validation_status`、`business_status`、`is_demo`、`rights_status` 及有效期。已核验还必须提供 `verified_at`、`reviewer`、`verification_method` 和完整有效区间。非来源实体的 verified 记录必须有来源；来源不能嵌套引用。时间必须带时区，当前业务日期以 Asia/Shanghai 为准，不支持跨日营业或演出区间，应拆分。

`unverified`、缺坐标、缺结束时刻、过期、取消、演示、许可未知均可作为显式候选保存。2026-10-24 新增独立的 `planning_status`：默认 `candidate` 仍不能进入规划；有来源、非演示且非禁止使用的资料，经维护导入可标为 `reference`，允许 MVP 参考规划，但仍保持 `unverified`，不伪造核验元数据。`reference + internal_only` 可用于内部规划及明确标注参考状态的展示；`prohibited`、演示资料和缺地点坐标仍不能据此生成路线。来源字段及核验声明仍需人工真实性审查，格式校验不是自动事实查证。

参考数据的高德 `provider_estimate` 会连同规划缓冲、报价有效期进入候选；过期需重新查询。营业、供应、入口及节目场次的不确定性仍按各自证据保留，`ready` 只证明这条事实满足当前入口的可用性检查，**不证明其关联整条路线已核验或保证现场执行**。团队与既有采集资料的正式 MVP 合并方式见 [MVP 数据接入清单](MVP_DATA_INVENTORY_20261024.md)。

同一 `(kind,fact_id)` 的变更必须逐版递增；完全相同的重试不产生新修订。不可静默删除事实，撤销用新版本 `business_status=cancelled` 表达。跨实体引用、类型错误、地点归属循环、重复 ID 或版本冲突使整批拒绝，原库保持不变。当前不提供多来源冲突裁决、审核后台、营业规则继承或道路 GeoJSON 导入；原始冲突证据应继续保留在采集包，解决后再升版。

## 导入步骤

先归一化真实证据，再使用实际目标库校验，可关联库内已有事实：

```bash
cd /Users/mac/Desktop/大挑/kaifeng-tourism-mvp
.venv/bin/python scripts/import_city_facts.py /absolute/path/to/facts.json \
  --database /Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/tourism.sqlite3
```

默认 dry-run，只在内存副本完整验证，不修改所选库；不传 `--database` 则按空库验证。检查返回的 changed、unchanged、ready 和 issues，原始证据、有效日期和公开使用许可仍需人工核验。确认正式数据后显式应用：

```bash
.venv/bin/python scripts/import_city_facts.py /absolute/path/to/facts.json \
  --database /Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/tourism.sqlite3 --apply
```

应用前再次验证数据库最新版本，事务内保存当前值和修订。维护 CLI 不启动应用、不调用网络、不读取收费密钥；只授权本机维护者使用，没有游客 HTTP 导入接口。单包最多 10MB、2000 条记录。

契约更新后重新生成：

```bash
.venv/bin/python scripts/import_city_facts.py --schema > docs/contracts/city-facts-v1.schema.json
```

## 城市状态接口与门禁

| 方法与路径（前缀 `/api/city/v1`） | 行为 |
| --- | --- |
| `GET /capabilities` | 当前工程能力，含参考规划的实际接线状态 |
| `GET /facts?kind=place` | 返回 permitted 及可用于 MVP 的 reference/internal_only 记录，附 ready/issues；不包含游客状态 |
| `POST /trips` | 创建部分或完整草稿，服务端分配身份 |
| `GET /trips`、`GET /trips/current`、`GET /trips/{id}` | 仅当前会话可读，恢复权威版本 |
| `PATCH /trips/{id}` | 类型化保存、手动当前位置、完成、跳过或状态转换 |
| `POST /trips/{id}/changes` | 生成 15 分钟候选，包含影响说明，不修改当前安排 |
| `POST /trips/{id}/confirm` | 重查会话、版本、事实修订、候选时效及全部业务校验，再提交 |

写请求需要 `request_id`；更新另需 `expected_version`。请求 ID 绑定会话、操作和请求体；重复返回原结果，复用为其他内容拒绝。旧版本返回 409，不能覆盖新页面。事务将状态、最小事件和幂等回执一起写入；异常回滚。重要时间、起终点变更及影响未完成锁定活动的操作须预览并确认。

`DRAFT -> ACTIVE` 表示开始记录用户实际游玩，需要基本字段及活动齐全，不表示已规划或通过事实硬约束。之后可暂停、结束或取消；终态不可恢复游玩。仅 ACTIVE 可按下一活动顺序完成或跳过；完成时间不得来自未来或越过行程范围，已执行前缀不能通过保存草稿改写。手动锚点只接受当前有效、已核验、非演示地点，默认 15 分钟新鲜度，不接受连续 GPS。

## 留存、导出、删除与迁移

复用 M7 的 HttpOnly 会话、24 小时滑动 TTL、同源写入校验、属主核验与独立删除台账。新表全部通过会话和城市行程级联删除；已结束 72 小时不可读并清理，最小操作事件 30 天清理，过期候选清理。归档不会重新开始 72 小时计时。事实库是公共业务资料，不随游客删除。

`/api/v1/memory/export` 增加 `city_trips`，包含需求、当前手动锚点、有效候选、最小事件及幂等回执。回执中有状态快照，可能保留先前手动地点，属于同一私有会话的派生数据，随会话/行程删除；不写通用日志或发送给 LLM。关闭对话留存时清除城市候选，并清除对应候选回执内容，防止重试恢复旧候选；关闭聊天留存不等于删除用户选择保存的行程。删除广播清空其他标签页及迟到请求的界面状态。

迁移只新增 6 张表和 `conversation_sessions.active_city_trip_id`，不改变旧 `active_trip_id`，不自动认领旧路线。启动和恢复工具均幂等执行。运行库已增量建表；对迁移前备份的副本执行迁移后，逐表、逐旧字段比较全部旧行，并与运行库对照通过，见 [迁移检查](reports/city-migration-check-20261021.json)。

代码及迁移前库备份在 `.backups/city-storage-20261021/`，包含私人数据，不对外发布。恢复只能生成新的候选库并应用**当前**独立删除台账及 TTL，禁止直接覆盖运行库、禁止回滚删除台账；命令见 [M7_MEMORY.md](M7_MEMORY.md)。软件级重放已测试，不等于云备份或磁盘副本已物理擦除。

## 下一步依赖

先核验一条有限链：景区出入口与必要道路、目标日期完整场次、一处餐饮地点与真实供应关系、营业和再入园规则、返程点。城市联合求解、全程复核、剩余重规划及地图/助手共享状态已有软件接线。下一步是将核验交通资料接入正式引擎，完成真实数据与现场验收；高德参考问路的真实成功不能替代这一门槛。
