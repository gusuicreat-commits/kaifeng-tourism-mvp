# M7 访客记忆框架验收记录

> 历史阶段记录：仅描述原日期的软件、操作与验收边界，不作为当前开发待办或最新完成声明。当前状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。


> 适用范围说明：M7 验收仍然有效地描述访客会话、版本、授权、删除与单景区状态框架，但它不等于城市级 CityTrip 已实现。完成进度后的新起点、园内外连续行程和跨场景变更属于 v2 待开发范围，详见 [M7_MEMORY.md](../../M7_MEMORY.md) 与 [DEVELOPMENT_DIRECTION.md](../../DEVELOPMENT_DIRECTION.md)。

日期：2026-10-20。依据用户提供的 `reference/M7_Memory_Architecture_v1.pdf`，逐项对照其P0及工程边界。当前结论：**访客模式的记忆与行程状态框架可运行、自动化验收通过；不是整份PDF中所有P1/P2及正式部署能力均已完成。**

## 实际运行结果

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| `./scripts/check.sh` | **167项后端测试通过**，TypeScript检查及Vite生产构建通过 | 原96项回归 + 新71项M7用例；其中40项为合成参数化约束保持剧本 |
| `npm run test:e2e`（指定已安装Chromium） | **33项通过** | 28项浏览器用例 + 5项Workflow状态用例；新增6项M7真实前后端闭环 |
| 最终告知文案更新后 | 构建通过，最终33项前端回归再次全部通过 | 用户发送前可看到对话用途、期限及关闭入口 |
| 实际独立进程重启 | **通过** | 临时数据库启动两个不同OS进程，保留cookie后恢复同一trip、路线ID、版本和对话；`research/m7-process-restart.json` |
| 100次上下文读取 | 本机p50 **2.622ms**，p95 **6.771ms**，最大12.597ms | 临时SQLite / FastAPI TestClient，含授权及事实查询，不含外部LLM；`research/m7-local-benchmark.json`，不代表目标部署性能 |
| 本机8017服务 | 已重启到M7代码；健康检查200；未带cookie的个人会话接口401（预期） | 原项目进程经cwd/命令核对后重启；未终止其他项目 |
| 迁移前原数据 | **全部原行未改写/删除** | 原5条景区、2份路网、504条观测、0条日程、4条旧路线均保留；`research/m7-migration-check.json` |
| 可视检查 | 已查看390px记录管理及候选确认页；原320/390px布局回归通过 | `screenshots/m7-memory-mobile.png`、`m7-memory-desktop.png`、`m7-proposal-mobile.png` |

测试服务器、基准及进程重启验证都使用临时数据库与独立删除台账，并显式关闭付费模型。没有在开发库里写测试游客、假日程或模拟个人消息。已有1条Starlette/httpx弃用提示，未影响测试通过。

## 规格对应与验收状态

| PDF要求 | 实现 / 实际断言 | 结论 |
| --- | --- | --- |
| 先盘点再增量实施 | `M7_MEMORY.md`现状差异和最小变更图；变更前源码及SQLite一致性快照 | 已做 |
| 会话连续和恢复 | 服务端随机cookie，滑动24小时；10轮对话、刷新与独立进程重启保持结构化约束 | 访客范围通过 |
| 业务单一事实源 | trip_states与M2独立；旧摘要中的“999分钟/已完成/昨天开演”不能覆盖约束或当日日程 | 通过 |
| 同一trip路线版本 | 重规划维持trip_id，route_version单调递增；显示当前路线编号；序号按该版真实停留点解析 | 通过 |
| 三阶段写入 | 消息仅产生候选；用户确认后写草稿或求解；候选过期/旧版本不可提交 | 通过 |
| 乐观锁 | 真实并发修改/并发重规划，同一expected_version只成功一个，另一个409 | 通过 |
| 幂等 | 同一重规划/聊天请求重复10次只提交一次；相同ID不同内容409；删除重试同一回执 | 通过 |
| 原子路线提交 | 求解失败保留旧版；数据库在路线插入之后故障也回滚路线、版本、事件和回执 | 通过 |
| 权限隔离 | 两个独立访客不能互读/更新/切换/解释他人的trip/plan；客户端user_id不被接受 | 访客范围通过，真实账户未接入 |
| 同会话多trip | 显式active_trip_id；新trip不继承临时偏好；消息与摘要按trip筛选 | 通过 |
| 有界上下文 | 最近8条同trip消息，≤600字摘要，≤12000字符Envelope；无原始GPS/令牌/音频 | 通过 |
| 临时/长期区分 | “今天少走路”只在本trip；user_preferences始终未被自动写入；未认证长期写入401 | P0边界通过，P1保存功能未完成 |
| 语音文字一致 | 转写先在界面核对，发送后同session/trip、channel=voice；含糊/旧版序号不得改点 | 通过，未宣称真实ASR准确度 |
| 必去与必看保持 | 模式调整后必去点、总时长、景区及必看场次不丢；M2场次撤销后确认失败并保留旧版 | 合成日程夹具通过，非真实演出验证 |
| 过期即拒读 | 未等后台清理即可拒绝过期会话/行程；幂等回执也不能绕过72小时期限 | 通过 |
| 可关闭、导出、删除 | /memory管理页；关闭清对话/摘要/候选及旧回执正文；删除清在线数据和同源标签页/语音暂存 | 通过 |
| 删除后恢复不复活 | 恢复旧运行库，独立台账先拒读，启动重放删除；台账缺失不重建而是503 | 通过，依赖保留当前台账的恢复纪律 |
| 安全恢复工具 | 仅生成新的恢复候选库、应用台账与TTL、拒绝覆盖已有文件 | CLI实际运行通过 |
| 结束及完成进度 | 受控状态机、仅当前路线点可标完成、已结束不能自动ACTIVE；已完成事实不可清空 | API通过；真实起点接续未完成 |
| PostgreSQL/PostGIS / RLS | 当前仓库SQLite适配，未运行PG或冒充PG验证 | 未完成，正式迁移单列 |
| 登录、长期偏好/历史归档、跨设备 | P1表及拒绝式边界，不接收客户端伪造身份，不自动迁移匿名到账户 | 未完成 |
| 备份加密和物理生命周期 | 已提供删除台账、恢复拦截与清理接口；未搭建云备份或30天自动物理销毁 | 未完成，不计入删除已物理完成 |
| 真实用户/地图/客流研究 | 本轮只检验软件功能，沿用清楚标注的示意路网 | 未验证 |

“40剧本”和约束保持断言均为工程合成用例，不能改称40个真实业务实测或真实用户≥95%指标；本次也没有模型理解准确率、正式环境p95或真实客流改善率结论。

## 你可以直接执行的界面验收

1. 打开 `http://127.0.0.1:8017/assistant`。选万岁山、具体日期和13:00、180分钟，选文化/拍照兴趣及一个必去示意点，确认生成路线。
2. 在结果页点“调整游玩需求”，核对时间、必去点与当前路线编号仍在；输入“今天少走路”。**确认前路线版本不变**，确认后才生成新版本。
3. 从显示的路线列表中选一个**非必去点**，输入“第N个点不去了”，先看对应名称再确认。刷新后取消结果保留，原必去点和总时长不变。若取消的是必去点，确认文案说明会同时取消该项要求。
4. 问“为什么这样安排？”，确认能解释已有路线且不会重写行程；原有缺项问答仍可继续。
5. 打开语音工具，手动编辑转写文字并“发送到小汴梁核对”；确认文字与当前行程共用上下文，**不自动提交调整**。
6. 点“查看与管理记录”。关闭留存后对话被清除、已确认行程保留；可导出。点击删除并明确确认，另一个标签页的旧路线和临时语音文字也应清空；重新开始后不恢复旧记录。
7. 用独立浏览器身份打开私有路线链接，应拒绝读取。M7前的无属主旧路线返回410；数据库原行保留而不自动认领。

本机默认无已核验当日演出，界面只承诺场地游览；不要把示意武侠剧场选点验收说成已验证真实演出观看。

## 复现命令

```bash
./scripts/check.sh

cd frontend
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Users/mac/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' npm run test:e2e
cd ..

.venv/bin/python scripts/benchmark_memory.py --output docs/research/m7-local-benchmark.json
.venv/bin/python scripts/verify_memory_restart.py --output docs/research/m7-process-restart.json
```

保留代码/数据回退点：`.backups/memory-m7-20261020/`。**日后有个人记录或删除事件后，不得直接用旧数据库覆盖运行库**；要先保留当前删除台账，使用新候选库恢复工具并复核。开发前快照不包含后续新增个人记录，不能代替删除台账或正式加密备份策略。

## 文件清单

新增：

- `backend/app/memory/{__init__,schema,session,storage,context,trips,coordinator,api}.py`
- `backend/tests/test_memory.py`
- `frontend/src/memoryTypes.ts`、`voiceDraft.ts`、`pages/Memory.vue`
- `frontend/tests/memory.spec.ts`
- `scripts/{memory_maintenance,restore_memory_backup,benchmark_memory,verify_memory_restart}.py`
- `docs/M7_MEMORY.md`、本验收单、`docs/reference/M7_Memory_Architecture_v1.pdf`、`docs/research/m7-*.json`、`docs/screenshots/m7-*.png`

修改：

- `backend/app/{main,models,planner,language}.py`：路由注册、私有路线保护、排除点与受限上下文解析。
- `backend/tests/{conftest,test_mvp}.py`、`scripts/browser_server.py`：隔离测试台账/模型调用；重启测试须携带原有效cookie。
- `frontend/src/{api,types,main,style}.ts/css`、`components/{GuidedPlanner,VoiceInput}.vue`、`pages/Result.vue`：会话/行程接口、候选确认、语音衔接、删除跨标签页清理和结果版控。
- `frontend/tests/interview-helpers.ts`、`algorithms.spec.ts`、`interview.spec.ts`：按新候选确认和M7规划契约更新回归断言。
- `AGENTS.md`、`README.md`、`docs/{PRD,PLANNING_WORKFLOW}.md`、`.env.example`、`.gitignore`：事实、契约和运维边界。

没有新增向量数据库、Mem0/Zep、独立记忆Agent或未经需求证明的依赖。第三方库及许可证沿用项目锁定清单。
