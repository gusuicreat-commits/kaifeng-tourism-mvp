# 开封主城区旅游数据质量报告

- run_id：`run_c85cfe6bbe80`
- 生成时间：2026-10-24T17:08:21+08:00
- 采集日：2026-10-24
- 指标口径：[docs/DATA_QUALITY_REPORT_SPEC.md](docs/DATA_QUALITY_REPORT_SPEC.md)

## 汇总

- 记录总数：0；来源登记：205；重复：0；冲突：0；失败：1；待核验任务：0
- bundle dry-run：{"status": "dry_run", "received": 0, "changed": 0}

## 类别 × 城区 覆盖

| 类别 |  |
| --- |  |

## 八项指标

### 1. 来源可追踪率

- `total_records`: 0
- `with_source`: 0
- `registry_matched`: 0
- `hash_matched`: 0
- `rate`: 0.0
- `missing_source_records`: 无
- `unregistered_source_keys`: 无
- `hash_unverified_records`: []

### 2. 关键字段完整率

- `per_category`: []
- `note`: 缺失即缺失，null 不被默认值替换；缺口用于补采排队。

### 3. 坐标与坐标系适配率

- `places_total`: 0
- `with_coords`: 0
- `system_wgs84`: 0
- `system_gcj02`: 0
- `system_unknown`: 0
- `demo_coords`: 0
- `rate`: 0.0

### 4. 日期有效性

- `rules_total`: 0
- `with_business_date`: 0
- `date_in_future`: 0
- `date_is_collection_day`: 0
- `date_null`: 0
- `collection_day_records`: 无

### 5. 路网/空间连通证据

- `edges_total`: 0
- `with_source_locator`: 0
- `connected`: 0
- `disconnected`: 0
- `unchecked`: 0
- `note`: 本轮未采园内道路与入口衔接候选；unchecked 不得声称为可导航。

### 6. 真实入口覆盖率

- `priority_scenics`: 0
- `with_entrance_candidate`: 0
- `entrance_candidates_total`: 0
- `rejected_fake_entrances`: 0
- `note`: 停车场/地图中心点不得计为入口候选。
- `orphan_entrance_candidates`: []

### 7. 商户定位与供应证据

- `vendors_total`: 0
- `with_location`: 0
- `supply_total`: 0
- `evidence_strong`: 0
- `evidence_candidate_only`: 0
- `note`: 平台推荐菜只算候选，不得升级为供应事实。

### 8. 冲突处理完整率

- `conflicts_total`: 0
- `preserved_all_values`: 0
- `resolved`: 0
- `silently_picked`: 0
- `items`: 无
- `note`: 解决须注明依据；本轮 0 条已解决。

## 红线自检

- `fabricated_data`: 0
- `ai_completion`: 0
- `bypass_import_channel`: 0
- `bypass_waf`: 0
- `note`: 自检口径：本工具只做代码抽取与候选生成，未写运行库、未绕过访问控制。

## 缺口优先级

- [high] 来源可追踪率未达 100%：hash_matched=0/0
