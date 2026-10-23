# 团队交接数据（成员1/成员2 工作簿 v1.1）

## 这是什么

2026-10-23 从微信临时目录复制到仓库的团队数据工作簿，及其转换产物。

| 文件 | 说明 |
| --- | --- |
| `汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx` | 原始工作簿（536 KB，SHA256 `ff1138a0…f0f8c4`） |
| `city-facts-teamhandoff-20261023.json` | 转换出的 city-facts/1 候选包（469 条记录） |
| `../../scripts/import_team_workbook.py` | 转换器，可重复执行 |

## 转换结果（2026-10-23）

| kind | 条数 | 来源工作表 |
| --- | --- | --- |
| source | 177（7 交接溯源 + 170 声明来源） | 18_source_records |
| place | 68（16 景区/街区/夜市 + 32 餐馆门店 + 20 入口地点） | 10/16/11 |
| vendor | 32 | 16_food_vendors |
| food | 26 | 15_food_items |
| supply | 48 | 17_vendor_foods |
| entrance | 20 | 11_place_entrances |
| program | 98 | 13_programs |

## 硬性边界

- **全部 469 条均为 `unverified` + `internal_only`**：工作簿证据文件（`evidence/` 目录）不在仓库内，无法复核原文，任何一条都不能升级 verified。
- **全部 170 条声明来源权限为 `public_view_only`**：可内部核对引用，不可对外转载，图片不可作产品素材。
- **dry-run 结果**：`status=dry_run, received=469, changed=469, ready=0`。`ready=0` 是预期且正确的——候选不等于可用事实。
- **未转换**：`12_open_rules`（19 条周期规则；city-facts/1 要求具体 `business_date`，周期规则须按目标游玩日逐日实例化，不能伪造日期）、`14_show_sessions`（v1.1 为空表）、`19_change_log`、媒体表 22/23/24（无对应 fact kind，保留在 xlsx 归档）。
- 坐标：仅 17 家餐馆门店有 GCJ-02 坐标；16 个景区/夜市和 20 个入口全部无坐标（`LOCATION_UNKNOWN`）。
- 餐饮分店已分开建档（黄家老店 3 店、邢家锅贴 2 店、宋都禾源 2 店、宋园 2 店），转换器不合并同名门店。

## 重复执行

```bash
.venv/bin/python scripts/import_team_workbook.py \
  "data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx" \
  --out data/team-handoff/city-facts-teamhandoff-20261023.json
.venv/bin/python scripts/import_city_facts.py data/team-handoff/city-facts-teamhandoff-20261023.json
```

第二步永远是 dry-run；写入运行库必须显式 `--database data/tourism.sqlite3 --apply`，且应发生在人工核验把候选升级之后。

## 后续待办

1. 向团队索取 `evidence/` 原始文件包，按 18 表 SHA256 复核后，逐条升级 validation_status。
2. 12_open_rules 的 19 条周期规则需要一个"目标游玩日实例化器"（输入规则+日期 → 输出 city-facts/1 open_rule），属于后续数据工程任务。
3. 14_show_sessions 为空是最大缺口：目标日期演出场次仍为零。

## 软件交接入口

Windows启动、环境与验证见 [HANDOFF.md](../../HANDOFF.md)；需向资料提供者补交的证据及核验事项见 [TEAM_DATA_GAPS.md](../../docs/TEAM_DATA_GAPS.md)。工作簿转换所需openpyxl已纳入项目数据依赖清单。
