# 开封扩展资料交接说明

## 这是什么

这是一份 **2026-10-23 的开封旅游资料候选包**，用于下一轮数据采编、来源核对和产品讨论。它不是正式导航库，也不是已验证的运营事实。

交付目录：`collection-20261023-expanded/`

关键边界：

- 目标游玩日：未指定，`target_business_date=null`
- `validation_status`：全部为 `unverified`
- `rights_status`：全部为 `internal_only`
- `is_demo`：全部为 `false`
- `formal_acceptance`：全部为 `false`
- 可执行路线：0 条
- 运行库导入：仅做过 dry-run；未写入 `city_facts`

## 接收方先读什么

按以下顺序读取，先看结论和边界，再按需查原始证据：

1. `HANDOFF.md`（本文件）
2. `reports/expanded-report-20261023.md`
3. `reports/expanded-summary.json`
4. `structured/a-level-directory-expanded.json`
5. `structured/places-expanded.json`
6. `structured/conflicts-expanded.json`
7. `structured/opening-rule-candidates-expanded.json`
8. `structured/ticket-rules-expanded.json`
9. `structured/entrances-expanded.json`
10. `structured/program-catalog-expanded.json`
11. `structured/vendors-food-candidates-expanded.json`
12. `structured/supply-candidates-expanded.json`
13. `structured/transport-expanded.json`
14. `structured/coverage-17-expanded.json`
15. `structured/source-manifest-expanded.json`

只有需要核对原文、哈希或证据等级时，才读取 `raw/`。

## 文件怎么对应

| 想回答的问题 | 优先文件 |
| --- | --- |
| 有哪些 A 级景区候选 | `structured/a-level-directory-expanded.json` |
| 地点、餐馆、夜市、车站有哪些 | `structured/places-expanded.json` |
| 哪些规则互相冲突 | `structured/conflicts-expanded.json` |
| 开放时间/停止入园候选 | `structured/opening-rule-candidates-expanded.json` |
| 票价、预约、票效候选 | `structured/ticket-rules-expanded.json` |
| 哪些门被明确命名 | `structured/entrances-expanded.json` |
| 节目目录和页面时刻 | `structured/program-catalog-expanded.json` |
| 菜品资料 | `structured/foods-expanded.json` |
| 餐馆分店、平台营业/人均 | `structured/vendors-food-candidates-expanded.json` |
| 哪些门店-菜品关系有供应证据 | `structured/supply-candidates-expanded.json` |
| 公交、旅游线、车站出口状态 | `structured/transport-expanded.json` |
| 17 类资料的缺口 | `structured/coverage-17-expanded.json` |
| 来源、采集时间、限制、哈希 | `structured/source-manifest-expanded.json` |
| 原始搜索/读取证据 | `raw/` |
| 严格导入校验结果 | `reports/city-facts-dry-run.json` |
| 可视化审阅 | `review.html`、`reports/review-desktop.png`、`reports/review-mobile.png` |

## 接收方不得做的事

- 不得把 `2026-10-23` 写成游客游玩日或营业业务日。
- 不得把搜索结果摘要说成运营方官网原文。
- 不得把平台推荐菜、品牌菜名或用户点评当成目标日门店供应证明。
- 不得把地图 POI 中心点、停车场或子 POI 当成游客入口。
- 不得把历史节庆时刻、2022/2025 服务语境或第三方平台时刻外推为目标日规则。
- 不得补造演出结束时间、排队时间、公交班次、餐厅最后接单、核验人或现场走查。
- 不得把 `ready=false` 的候选标成可执行路线。

## 当前可用的结论

- 24 个 A 级景区名录候选，其中 5A 1 个、4A 11 个、3A 6 个、2A 6 个。
- 7 家具体餐馆分店候选；20 条门店-菜品关联仍是 `candidate_not_supply_proof`。
- 清明上河园 154 条节目目录、开封府 24 条页面条目；均未成为目标日 `ShowSession`。
- 多个核心景区存在内部或跨来源时间冲突，冲突表保留原值，不强行合并。
- 来源清单、原始文件和 SHA-256 已完成校验；首批包未被覆盖。

## 正式验收前必须补什么

1. 目标日期、最早到达/最晚离开时间、必看演出、用餐时段和忌口/预算。
2. 目标日景区入口通行、开闭园、停止入园、票效、预约和出园再入规则。
3. 目标日节目场次、结束时刻、提前到场和取消状态。
4. 具体餐馆目标日营业、最后接单、带门店名的菜单/票据和供应证明。
5. 车站出口到公交上客点、公交到景区入口、入口到舞台的实际道路和耗时。
6. 一次完整现场走查，包含临时休息、跳过一站后继续安排和改线记录。
7. 真实维护负责人、复核日期和有效日期。

## 交接校验

目录内 `MANIFEST.sha256` 记录文件哈希。接收方解压后可在目录根执行：

```bash
shasum -a 256 -c MANIFEST.sha256
```

如果清单中的文件缺失或哈希不匹配，先停止使用并重新获取包，不要继续基于残缺副本补资料。

