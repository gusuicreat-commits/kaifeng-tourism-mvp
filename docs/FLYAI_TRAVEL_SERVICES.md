# FlyAI 与旅行服务接入（2026-10-24）

## 交付结论

已在主目录增量接入 FlyAI 官方酒店搜索、统一候选模型、既有高德 POI 和同一城市助手内的候选卡片。支持保存本次旅行的结构化查询条件、候选意向预览、确认、撤销/恢复、刷新恢复，以及会话导出/删除。未重写 CP-SAT、既有素材篮或正式行程确认。

**这是当前数据阶段的真实服务查询与候选意向管理，不是附件全部 P0/P1/P2 已完成。** 候选意向确认不生成正式路线；当前正式运行资料不齐，现有引擎是同日城市活动模型，没有多晚住宿活动、房型报价与跨日住宿依赖模型。本轮不把未知填零，不把酒店搜索结果自动导入正式事实库。

## 真实调用与实际字段

[脱敏机器回执](reports/flyai-live-20261024.json)记录真实外部调用与临时数据库内的保存结果。没有支付、下单或正式事实导入。Key 只在后端本地 `.env`；不进入前端、文档或Git。

| 查询 | 本轮结果 | 证明范围 |
| --- | --- | --- |
| 开封酒店，2026-10-25 入住、10-26 退房、每晚200元上限 | 11 家 | 官方 CLI 搜索可用；不是所选房型、人数和库存保证 |
| 清明上河园停车场 | 10 个高德 POI | 实际地点候选；不是酒店自有/免费停车、实时余位或入口路线 |
| 开封灌汤包餐厅 | 10 个高德 POI | 关键词命中；不证明实际供应灌汤包、营业、口味或价格 |
| 开封站 → 清明上河园步行 | 5205米、4164秒的高德估计 | 本次参考查询；入口未核验，不是正式导航 |
| 酒店候选 → 影响预览 → 确认意向 | 保存成功，route_version仍为0 | 证明候选工作区流程；不证明正式住宿行程已生成 |

实际非空酒店字段包括 `shId/name/address/price/mainPic/detailUrl/star/decorationTime/interestsPoi/latitude/longitude`；`brandName`部分存在；`rate`本轮为空。完整字段集合及原始单条样例见机器回执。经纬度的坐标系未经确认，标准模型未将其用于导航。

`price`只有展示起价，统一保存为 `price_display/nightly_from_fen`，`total_fen=null`；不能据此计算多晚、多间、人数适配后的保证总价。窗户/外窗、干湿分离、早餐、免费停车、卫生、隔音、充电、无障碍和取消政策全部保持 `unknown`，记录酒店/房型范围、查询时间和来源字段缺失。第三方文本只作为普通文字展示，不进入工具指令。

依据：[FlyAI官方Skill与酒店命令](https://github.com/alibaba-flyai/flyai-skill/blob/main/skills/flyai/references/search-hotel.md)。TOP [酒店详情接口](https://open.fliggy.com/docs/api.htm?apiId=23475)文档列出房型、窗型、面积和最大入住人数，同时要求独立TOP AppKey及签名；另列价格与评论接口。未持有或验证该平台应用权限，本轮没有混用FlyAI Key调用TOP，也不声称这些字段或评论已获得授权。

## 主要代码与边界

| 文件 | 职责 |
| --- | --- |
| `backend/app/city/hotels.py` | 独立FlyAI Provider与Pydantic模型；白名单字段、URL域名校验、未知属性；官方CLI参数数组调用，无shell；12秒超时、2并发上限、进程内每秒最多1个新请求、3分钟缓存、固定开封目的地 |
| `backend/app/city/travel_services.py` | 按旅行归属的版本化工作区；常见中文条件提取；复用高德POI；候选意向预览/确认/撤销；并发、过期、跨旅行及终态门禁 |
| `backend/app/city/agent/service.py` | 明确服务查询分流；复用现有任务、历史与取消检查；明确起终点问路及原餐饮规划优先保留 |
| `backend/app/city/storage.py`、`state.py` | 增量表初始化、旅行外键级联删除和导出 |
| `frontend/src/components/TravelServiceCards.vue` | 宋风候选卡片、展示价与缺证提示、影响预览、意向确认与撤销、外部详情/预订链接 |
| `frontend/src/components/CityAgentChat.vue` | 同一城市聊天入口显示候选，不开第二个AI页面 |
| `scripts/install_flyai.py` | 下载固定官方CLI 1.0.16、验证已审查文件SHA-256，只安装到本项目忽略目录 |
| `scripts/verify_travel_services.py` | `--live`显式运行真实Provider探针；使用临时数据库，不污染运行库 |

已有 AMap Provider 提供自身超时、限流和异常分类。FlyAI Provider不盲目重试未知错误，失败回传空候选及有界状态，用户可重试；不使用CLI内置试用Key降级。CLI的共享机器配置、DEBUG端点和签名覆盖变量不会用于本服务调用。

## 可用交互与运行

本机已配置 `FLYAI_ENABLED=true`、`FLYAI_API_KEY`、`FLYAI_CLI_PATH`。高德继续使用既有 `AMAP_WEB_SERVICE_KEY`、`AMAP_LOCATION_SEARCH_ENABLED`、`AMAP_TERMS_REVIEWED`、`AMAP_LOCATION_POI_API_VERSION`、`AMAP_REFERENCE_ROUTES_ENABLED`；不修改现有密钥值。

```bash
# 在 kaifeng-tourism-mvp 根目录，新环境需先完成原项目 setup
.venv/bin/python scripts/install_flyai.py
npm run build --prefix frontend
.venv/bin/python scripts/start.py
```

默认同源地址为 `http://127.0.0.1:8017/assistant?context=city`，实际端口以本地 `.env` 为准。已有服务如果未自动重载，重启后再使用新API与新增环境变量。不要将Key配置为`VITE_*`。

示例：

- “明天找开封酒店，住2晚，两个人，1间房，200元以内，必须有窗户和干湿分离，最好免费停车，总预算1000元，自驾。”
- “换一个150元以内的，早餐也要有。”保留其他已提取条件。
- “搜索清明上河园停车场”或“搜索开封灌汤包餐厅”。
- “从开封站到清明上河园步行怎么走”继续走既有参考路线链路。

当前解析是常见表达的确定性增量支持，支持明确ISO日期、今天/明天/后天、住几晚/玩几天、人数房数、每晚上限、总预算、部分设施及交通偏好。**不声称支持任意组合语义**；完整复杂输入中的景点、餐饮时间、床型、过敏、混合交通等尚未全部接入同一个结构化解析与联合求解。候选意向不自动成为规划起点或长期偏好。

API 前缀 `/api/city/v1/trips/{trip_id}/services`：GET工作区，POST `/query`、`/preview`、`/confirm`、`/undo`。查询使用工作区 `expected_version`；预览确认另含 `request_id`；确认校验旅行版本、工作区版本、预览身份及3分钟候选有效期。确认和预览有幂等回执；撤销通过版本校验避免重复翻转。

## 验证与未完成项

本轮 `./scripts/check.sh`：后端 **692 passed**，TypeScript检查与生产构建通过。随后新增两项Provider参数/超时及隐私级联测试，旅行服务专项最终 **15 passed**；其余已包含在完整后端结果中，不相加。唯一测试警告为既有Starlette/httpx弃用提示。

手机端专项：**1 passed**，覆盖聊天输入、卡片、未知条件、预览、确认意向、刷新、多轮改价保留硬条件、撤销和无页面横向溢出；数据为明确标注的Mock候选。截图：[手机候选工作区](screenshots/travel-services-synthetic-mobile.png)。既有Agent浏览器回归 **7 passed**，使用原合成业务夹具。未运行默认全部浏览器套件，不改写其此前失败记录。

| 附件验收场景 | 本轮覆盖 | 尚缺 |
| --- | --- | --- |
| A 酒店深度搜索 | 真实候选、未知设施、软件硬条件不丢失 | 房型/套餐证据、最终条件匹配 |
| B 自驾停车 | 真实停车POI、费用/空位未知 | 停车至实际入口步行、停车时间入正式引擎 |
| C 更换酒店影响行程 | 候选意向差异、影响提示、原路线不改、撤销/恢复 | 多日住宿模型、正式地点映射、转场重算 |
| D 评价筛选 | 明确无评价证据，不制造评论 | 取得合法授权评论接口及覆盖率分析 |
| E 多轮对话 | 常见住宿条件、预算/交通保留及版本隔离 | 全量意图、与既有景点/餐饮时间约束统一 |
| F 外部故障 | Mock异常/空返回、真实subprocess超时处理、无假结果 | 线上故障注入、持续容量与跨进程限流 |
| G 全流程 | 真实查询→意向预览/保存，独立Mock浏览器流程；既有正式引擎回归 | 真实旅行的联合多方案→正式确认→途中住宿重算闭环 |

按当前项目时机，后续先补齐有限真实地点/入口/交通/营业及目标日场次，再给同日引擎增加有证据的住宿转场和费用输入；跨日住宿需单独设计版本化模型并验收。不能靠酒店起价、POI名称或一次真实请求成功替代这些准入条件。P1评价分析、精确住宿/停车总价、天气调整，P2订单同步/商业化均未在本轮实现。
