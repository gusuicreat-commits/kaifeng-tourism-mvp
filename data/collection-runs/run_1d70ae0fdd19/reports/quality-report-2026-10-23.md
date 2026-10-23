# 开封主城区旅游数据质量报告

- run_id：`run_1d70ae0fdd19`
- 生成时间：2026-10-23T23:52:09+08:00
- 采集日：2026-10-23
- 指标口径：[docs/DATA_QUALITY_REPORT_SPEC.md](docs/DATA_QUALITY_REPORT_SPEC.md)

## 汇总

- 记录总数：48；来源登记：25；重复：0；冲突：0；失败：0；待核验任务：14
- bundle dry-run：{"status": "dry_run", "received": 71, "changed": 71, "ready": 0, "issues": 71}

## 类别 × 城区 覆盖

| 类别 | 龙亭区 | 鼓楼区 | 顺河回族区 | 禹王台区 | 城乡一体化示范区 | 待核验 |
| --- | --- | --- | --- | --- | --- | --- |
| museum | 5 | 5 | 2 | 1 | 4 | 5 |
| street_block | 0 | 1 | 0 | 0 | 0 | 0 |
| night_market | 1 | 3 | 1 | 0 | 0 | 0 |
| food | 0 | 0 | 0 | 0 | 0 | 7 |
| restaurant | 5 | 5 | 0 | 0 | 1 | 1 |
| entrance | 0 | 0 | 0 | 0 | 0 | 1 |

## 八项指标

### 1. 来源可追踪率

- `total_records`: 48
- `with_source`: 48
- `registry_matched`: 48
- `hash_matched`: 48
- `rate`: 1.0
- `missing_source_records`: 无
- `unregistered_source_keys`: 无
- `hash_unverified_records`: []

### 2. 关键字段完整率

- `per_category`: [{"category": "museum", "fields_filled": 39, "fields_total": 44, "records_complete": 17, "records_total": 22, "required_fields": ["name", "district"]}, {"category": "street_block", "fields_filled": 2, "fields_total": 2, "records_complete": 1, "records_total": 1, "required_fields": ["name", "district"]}, {"category": "night_market", "fields_filled": 10, "fields_total": 10, "records_complete": 5, "records_total": 5, "required_fields": ["name", "district"]}, {"category": "food", "fields_filled": 7, "fields_total": 7, "records_complete": 7, "records_total": 7, "required_fields": ["name"]}, {"category": "restaurant", "fields_filled": 23, "fields_total": 24, "records_complete": 11, "records_total": 12, "required_fields": ["name", "district"]}, {"category": "entrance", "fields_filled": 1, "fields_total": 1, "records_complete": 1, "records_total": 1, "required_fields": ["name"]}]
- `note`: 缺失即缺失，null 不被默认值替换；缺口用于补采排队。

### 3. 坐标与坐标系适配率

- `places_total`: 41
- `with_coords`: 0
- `system_wgs84`: 0
- `system_gcj02`: 0
- `system_unknown`: 41
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

- `priority_scenics`: 40
- `with_entrance_candidate`: 0
- `entrance_candidates_total`: 1
- `rejected_fake_entrances`: 0
- `note`: 停车场/地图中心点不得计为入口候选。

### 7. 商户定位与供应证据

- `vendors_total`: 12
- `with_location`: 10
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

- [high] 地点坐标缺失：41 条无坐标
- [medium] 空间归属待核验：place_kfsbwg, place_kfsbwg_newgate, place_jingcha_museum, place_dezhuang_ceramic_museum, place_kaifeng_culture_hall_old, place_kaifeng_library, place_heyuan_jinxiang, food_guantangbao, food_siweicai, food_liyu_beimian, food_tongziji, food_chaoliangfen, food_yangrou_kangmo, food_xingrencha
