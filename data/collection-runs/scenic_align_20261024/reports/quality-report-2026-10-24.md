# 开封主城区旅游数据质量报告

- run_id：`scenic_align_20261024`
- 生成时间：2026-10-24T14:32:08+08:00
- 采集日：2026-10-24
- 指标口径：[docs/DATA_QUALITY_REPORT_SPEC.md](docs/DATA_QUALITY_REPORT_SPEC.md)

## 汇总

- 记录总数：12；来源登记：197；重复：0；冲突：0；失败：0；待核验任务：19
- bundle dry-run：{"status": "dry_run", "received": 25, "changed": 25, "unchanged": 0, "ready": 0, "issues": 25}

## 类别 × 城区 覆盖

| 类别 | 龙亭区 | 鼓楼区 | 顺河回族区 | 待核验 |
| --- | --- | --- | --- | --- |
| scenic | 6 | 4 | 1 | 1 |

## 八项指标

### 1. 来源可追踪率

- `total_records`: 12
- `with_source`: 12
- `registry_matched`: 12
- `hash_matched`: 0
- `rate`: 0.0
- `missing_source_records`: 无
- `unregistered_source_keys`: 无
- `hash_unverified_records`: ["pl_wb_000001", "pl_wb_000002", "pl_wb_000003", "pl_wb_000004", "pl_wb_000005", "pl_wb_000006", "pl_wb_000007", "pl_wb_000008", "pl_wb_000009", "pl_wb_000010", "pl_wb_000011", "pl_wb_000012"]

### 2. 关键字段完整率

- `per_category`: [{"category": "scenic", "fields_filled": 23, "fields_total": 24, "records_complete": 11, "records_total": 12, "required_fields": ["name", "district"]}]
- `note`: 缺失即缺失，null 不被默认值替换；缺口用于补采排队。

### 3. 坐标与坐标系适配率

- `places_total`: 12
- `with_coords`: 0
- `system_wgs84`: 0
- `system_gcj02`: 0
- `system_unknown`: 12
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

- `priority_scenics`: 12
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

- [high] 来源可追踪率未达 100%：hash_matched=0/12；12 条来源已登记但 raw 缺失或SHA-256 不匹配，需补原始证据文件
- [high] 地点坐标缺失：12 条无坐标
- [medium] 空间归属待核验：pl_wb_000004
