# 主版本地点与参考问路合并验收

2026-10-23，已切换至主目录的 5177 / 8017。8047 是独立测试副本，用户继续使用原 5177 地址及原会话。

## 合并与备份

使用三方基线及 SHA 核对，34 个目标文件无冲突；切换前再次检查未出现并行变化，429 个其他文件保持原字节。备份位于 `../kaifeng-main-merge-backup-20261023-161200`，包含源码归档、原配置和一致性数据库快照。没有复制隔离数据库或测试 fixture 到正式库。

主配置仅开启 AMAP_LOCATION_SEARCH_ENABLED、AMAP_TERMS_REVIEWED、AMAP_LOCATION_POI_API_VERSION=v5、AMAP_REFERENCE_ROUTES_ENABLED，原 Kimi 和地图配置保留。正式 OR-Tools 确认要求保留并沿用准入补强，参考问路不调用正式提交。

原启动器在后端退出时联动关闭前端，因此两项服务均重新启动。后续用户中断使进程停止，18:10 前恢复；当前后端及前端分别由本轮进程运行。

## 实际浏览器与调用链

在用户原 5177 助手会话发送“从开封站到清明上河园怎么走”：understand（明确起终点的确定性分流）→ search_locations（2次 /v5/place/text）→ query_reference_route（1次 /v5/direction/walking）。成功回执为 HTTP 200、status 1、infocode 10000。不请求游客 GPS，不生成正式路线。

16:49 首次实测显示约70分钟/5.2公里；恢复服务后的18:14查询显示约82分钟/6.1公里。二者均为各自查询时的高德参考返回，不是固定业务事实或保证到达时间。出入口仍未核验，正式行程仍受 needs_data 限制。结果仅在本次会话界面展示，刷新后可重新查询。

追问“这个和我的定位又没关系”实际调用 kimi-k2.6，HTTP200。首次追问仍受旧规划上下文措辞误导，将正式资料不足解释为参考路线不可用；随后缩小 planning_readiness 的适用范围为 formal_itinerary_only_not_reference_route，并修正系统说明。再次真实调用 HTTP200，明确回复指定两端无需定位、可以查参考路线，而正式行程仍待资料。两次调用均1次HTTP、0自动重试；最终总token3367。证据见 reports/main-merge-live-receipts.json。

## 数据保留

原40条聊天的ID、内容及其他业务字段全部保留，26条消息仅 expires_at 正常续期；新增8条验收对话，现48条。原2份城市行程逐行保持。所有其他原数据行保留，仅一个会话的活动/到期时间正常刷新，回执新增6条。正式路线、候选、事实和操作表未因参考问路新增；删除账本不变。详见 reports/main-merge-data-preservation.json。

## 验证边界

- 合并候选后端625 passed，合同检查和前端构建通过。
- 地点四状态浏览器4 passed；V3预览/确认等7 passed；地点及行程链定向10 passed。
- 完整默认前端84 passed / 4 failed / 1 skipped。3项旧页面断言在本轮合并前完整主代码中同样复现；另1项流程图断言未改代码的定向复测通过，尚未证明批量运行失败根因。没有声称全套全绿，也没有修改测试掩盖失败。
- 最后上下文修正后相关后端48 passed。地图已有回归通过，真实SDK专项未作为本轮验收。

## 回退

若需要回退，按 final-manifest 的目标文件清单从源码备份恢复，并移除本轮4个配置增量后重启服务；回退时必须再次检查新的并行改动。不要把备份数据库覆盖到当前库，否则会丢失合并后的用户记录。

截图：screenshots/main-merge-reference-live.png、screenshots/main-merge-kimi-live.png。
