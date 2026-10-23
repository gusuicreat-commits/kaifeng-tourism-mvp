# 已确认行程的纵向链式展示

2026-10-23。根据行程页空态区域的浏览器批注，接入确认后自动显示的纵向行程流程图；主项目 5177 开发页热更新可用。用户当前旅行仍无正式路线，保持原空态，没有添加示例站点或改写用户数据。

## 展示

- 起点 → 游览 / 演出 / 用餐 / 休息 → 返程，以卡片、竖线、箭头连接。
- 每站展示已有开始/离开时间、时长、类型、必去标记和状态；下一站突出显示。演出有已知节目名称时，同时展示节目与舞台名称。
- 交通标签只读取该正式行程相应方向的路段及出发/到达时间；缺段显示待核验，不猜方式或耗时。
- 途中重规划保留草稿中权威的已完成/跳过记录和实际时间；当前活动单独展示，不重复；其后使用剩余路线的路段。
- 页面仍使用 `usableItinerary()` 的属主旅行、版本、stale 和返程时效检查。未确认、失效或版本不匹配时回退原站点列表，不认证估算路线。
- “在地图查看”携带下一站 place 参数，与节点的“查看位置”及原下一站操作保持一致。

## 文件

- `frontend/src/components/ItineraryFlow.vue`：读取现有正式状态并渲染节点、方向连接与交通标签。
- `frontend/src/pages/JourneyPage.vue`：确认路线接入及地图下一站参数；无路线沿用原行为。
- `frontend/src/explorationApi.ts`：补充已有 ongoing 时间字段的可选类型，无后端契约变动。
- `frontend/tests/itinerary-flow.spec.ts`：合成快照展示与降级检查。
- `frontend/tests/agent-v3.spec.ts`：实际引擎确认后的时间检查改为定位活动节点，排除新起点节点。

## 验证

- `npm run build --prefix frontend`：TypeScript 和生产构建通过。
- `itinerary-flow.spec.ts` + `journey.spec.ts`：14 项浏览器测试全部通过。既有行程测试使用 `E2E_CITY_FACTS` / `E2E_OBJECT_BINDINGS` 显式装入临时测试库；新增展示快照标为合成，未写入运行库。
- `playwright.agent.config.ts --grep 'basket planning uses the existing engine'`：1 项通过，包含真实本地规划引擎预览、拒绝过期候选、显式确认、正式路线版本递增及返回新流程图核对时间。使用隔离合成事实/交通与模型替身，无外部模型请求。
- 覆盖 320 / 465 / 1200px 宽度、垂直顺序、分段方式/时长、完成及当前活动、未确认 / stale / 过期 / 错版本 / 缺交通段，以及原有手动修改、撤销、进度与地图跳转。
- 第一轮两条新增断言因 Playwright 多元素严格模式失败，已改为单根节点断言；原行程回归首次未带合成资料，之后显式启用；回归还发现无 place 参数的地图链接会显示默认地点，本轮增加下一站参数后全部通过。

截图 `docs/screenshots/itinerary-flow-synthetic-mobile.png` 为完整手机页；`itinerary-flow-chain-synthetic.png` 为合成链条预览（仅截图时隐藏悬浮导航和人物，运行界面保留）。

本轮只验收展示和已有软件链路，不代表真实开封地点、商家、交通或现场行程已经验收。源码基线保存在 `.backups/itinerary-flow-20261023/`。
