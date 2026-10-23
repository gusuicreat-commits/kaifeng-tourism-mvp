# 数据质量报告规范

版本 v1.0，2026-10-23。定义主城区旅游数据采集质量报告的字段与统计口径。生成器由 `collector/quality.py` 实现，每次 collect_run 结束或人工触发时输出到 `data/collection-runs/<run_id>/reports/quality-report-<date>.md`（及同名 JSON）。指标口径固定，跨批次可比。

## 8 项指标

以下 8 项对应 [DATA_SOURCES.md](DATA_SOURCES.md) 数据验收的八类检查，逐项给出字段、口径与通过判据。

### 1. 来源可追踪率 source_traceability

- 口径：结构化记录中 `source_keys` 非空、且每个来源在 source_registry 有登记、且对应 raw 文件 SHA-256 校验匹配的记录数 / 记录总数。
- 字段：total_records、with_source、registry_matched、hash_matched、rate。
- 通过判据：rate = 100%。无来源记录一律列出明细，不允许计入分母豁免。

### 2. 关键字段完整率 field_completeness

- 口径：按类别定义必填字段清单（`collector/config/categories.yaml`），逐类别计算 非空必填字段数 / 应填字段总数；同时报告"记录级完整率"（必填全齐的记录数/总数）。
- 字段：per_category[{category, fields_filled, fields_total, records_complete, records_total}]。
- 判据：不设硬门槛（缺失即缺失是允许的），但必须列出缺口明细供补采任务排队。**零填充造假**：null 不得被默认值替换。

### 3. 坐标与坐标系适配率 coordinate_readiness

- 口径：place 类记录中 经纬度成对存在 且 coordinate_system ∈ {WGS84, GCJ02}（非 unknown/local_demo）的比例；局部示意坐标单列。
- 字段：places_total、with_coords、system_wgs84、system_gcj02、system_unknown、demo_coords。
- 判据：报告真实比例；`local_demo` 必须同时 is_demo=true（契约强制），混入即失败。

### 4. 日期有效性 date_validity

- 口径：open_rule/show_session 记录中 business_date 明确且属于未来可核验窗口的比例；采集日被误当游玩日的条数单列。
- 字段：rules_total、with_business_date、date_in_future、date_is_collection_day（应为 0）、date_null。
- 判据：date_is_collection_day 必须为 0；其余如实报告。

### 5. 路网/空间连通证据 network_evidence

- 口径：涉及园内道路与入口衔接的候选中，具备来源定位（OSM 对象 ID 或官方导览定位）的比例；连通性判定状态分布（connected / disconnected / unchecked）。
- 字段：edges_total、with_source_locator、connected、disconnected、unchecked。
- 判据：unchecked 不得声称为可导航；无 access/foot 证据的 OSM 边不推断可通行。

### 6. 真实入口覆盖率 entrance_coverage

- 口径：重点景区清单（categories.yaml 标记 priority=high 的 scenic）中，至少有一个 place_type=entrance 候选入口且未把停车场/地图中心点冒充入口的景区数 / 重点景区总数。
- 字段：priority_scenics、with_entrance_candidate、entrance_candidates_total、rejected_fake_entrances。
- 判据：rejected_fake_entrances 必须如实计数（剔除假入口是质量行为，不是缺失）。

### 7. 商户定位与供应证据 vendor_supply_evidence

- 口径：vendor 记录中带地址或坐标的比例；supply 候选中 evidence_note 指向可追溯证据（菜单/商家公开确认/现场）而非平台推荐菜猜测的比例。
- 字段：vendors_total、with_location、supply_total、evidence_strong、evidence_candidate_only。
- 判据：candidate_not_supply_proof 类证据不得升级为供应事实；报告两级分布。

### 8. 冲突处理完整率 conflict_handling

- 口径：多来源同字段冲突的条目数、全部原值保留进 conflicts[] 的比例、已按适用日期/核验解决数、静默单选数（必须为 0）。
- 字段：conflicts_total、preserved_all_values、resolved、silently_picked。
- 判据：silently_picked = 0；resolved 须注明解决依据。

## 汇总区（报告头部）

| 汇总字段 | 说明 |
| --- | --- |
| run_id / generated_at | 关联运行与生成时间 |
| scope | 城区与类别覆盖矩阵（类别 × 城区计数） |
| totals | 记录总数、来源总数、新增/变更/失败数 |
| bundle_dry_run | import_city_facts.py dry-run 的 received/changed/ready/issues 摘要 |
| red_lines | 红线自检：虚构数据 0、AI 补全 0、绕过导入通道 0、绕过 WAF 0 |
| gaps_top | 缺口优先级清单（供下一轮 collect_task 排队） |

## 输出与留档

- Markdown 供人读，JSON 供跨批次对比；两者同一数据源生成，不手工修改。
- 报告数字必须可由台账与结构化产物复算；生成器接受 `--run-id` 或台账范围参数，结果幂等。
- 历史报告不覆盖，按日期递增命名。
