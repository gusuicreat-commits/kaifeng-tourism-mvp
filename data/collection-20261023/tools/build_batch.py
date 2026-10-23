#!/usr/bin/env python3
"""Build the 2026-10-23 Kaifeng candidate pack from preserved Exa read responses."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
STRUCT = ROOT / "structured"
REPORTS = ROOT / "reports"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def read_raw(name: str) -> dict:
    return json.loads((RAW / name).read_text())


def text_of(name: str) -> str:
    data = read_raw(name)
    return data["items"][0]["text"]


def sha(name: str) -> str:
    return hashlib.sha256((RAW / name).read_bytes()).hexdigest()


def captured_at(name: str) -> str:
    return datetime.fromtimestamp((RAW / name).stat().st_mtime, LOCAL_TZ).isoformat(timespec="seconds")


def dump(name: str, value) -> None:
    (STRUCT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_csv(name: str, rows: list[dict], fields: list[str]) -> None:
    with (STRUCT / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


SOURCES = [
    dict(source_id="src_qmsyun_program_20261023", file="qmsyun_program_20261023.json",
         name="清明上河园演出节目单", publisher="清明上河园", source_type="official",
         url="http://www.qmsyun.com/Mobile_page/o_program.php", published_at=None,
         applicability="页面页脚标注 2026-10-23 更新；未给目标日期场次结束/取消状态",
         locator="正文节目表与页脚", limitations="仅起始时刻，多数无结束时间；个别时刻晚于页面闭园时间"),
    dict(source_id="src_qmsyun_architecture_20261023", file="qmsyun_architecture_20261023.json",
         name="瑰伟建筑（迎宾门/金水门）", publisher="清明上河园", source_type="official",
         url="https://2023.qmsyun.com/wechat/q.php", published_at=None,
         applicability="未标发布日期，仅用于门名与相对方位",
         locator="迎宾门条目", limitations="没有门点坐标、开放方向和现场通行核验"),
    dict(source_id="src_qmsyun_gate_channels_20261023", file="qmsyun_gate_channels_20261023.json",
         name="2023 五一游园攻略（三入园通道）", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/o_newsinfo.php?softid=3168", published_at=None,
         applicability="2023 年五一假期，属历史季节性资料",
         locator="游园小贴士", limitations="不得外推为 2026 任意日期常开；票价也是历史活动价"),
    dict(source_id="src_qmsyun_song_banquet_20261023", file="qmsyun_song_banquet_20261023.json",
         name="清明上河·宋宴答疑", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/o_newsinfo.php?softid=4096", published_at=None,
         applicability="页面未标发布日期；仅作为当前售票页面候选，需目标日复核",
         locator="票价/购票/位置/时间/离席问答", limitations="未给本项目采用的采集日场次或现场履约证明"),
    dict(source_id="src_qmsyun_tokyo_dream_20261023", file="qmsyun_tokyo_dream_70min_20261023.json",
         name="大宋·东京梦华 70 分钟宣传页", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/o_newsinfo.php?softid=3363", published_at=None,
         applicability="早春促销语境；不得当作指定日期演出单",
         locator="演出时长与票价", limitations="未给具体日期、场次、结束时刻；页面日期不明"),
    dict(source_id="src_diyilou_home_20261023", file="diyilou_home_20261023.json",
         name="第一楼公司首页（门店地址和电话）", publisher="开封第一楼有限责任公司", source_type="operator",
         url="http://www.kfdyl.com/home/", published_at=None,
         applicability="公司官网当前可见；未标更新日期", locator="寺后街店地址/电话",
         limitations="官网没有分店营业时间与具体供应关系"),
    dict(source_id="src_diyilou_mingcai_20261023", file="diyilou_mingcai_20261023.json",
         name="第一楼名菜", publisher="开封第一楼有限责任公司", source_type="operator",
         url="http://www.kfdyl.com/mingcai/", published_at=None,
         applicability="品牌级菜品介绍，不是门店菜单", locator="名菜列表",
         limitations="不能证明寺后街店目标日期售罄/有售，也不能作为价格凭证"),
    dict(source_id="src_kaifeng_gov_diyilou_20261023", file="kaifeng_gov_diyilou_intangible_20261023.json",
         name="第一楼小笼灌汤包子（非遗介绍）", publisher="开封市人民政府", source_type="official",
         url="https://www.kaifeng.gov.cn/kfsrmzfwz/ssjfy/pc/content/content_1767488874204143616.html",
         published_at=None, applicability="菜品文化背景；不是门店供给证明", locator="菜品历史与特色",
         limitations="未提供门店菜单、营业和过敏信息"),
    dict(source_id="src_qunar_diyilou_20261023", file="diyilou_qunar_20261023.json",
         name="第一楼·中华老字号(寺后街总店)（去哪儿）", publisher="去哪儿", source_type="other",
         url="https://sight.qunar.com/3334343684", published_at=None,
         applicability="采集时页面显示固定营业时段，未给更新日期", locator="开放时间",
         limitations="第三方信息，未获商家确认；与携程/途牛不一致"),
    dict(source_id="src_ctrip_diyilou_20261023", file="diyilou_ctrip_20261023.json",
         name="第一楼灌汤包(寺后街店)（携程）", publisher="携程", source_type="other",
         url="https://gs.ctrip.com/html5/you/foods/kaifeng165/4926446.html", published_at=None,
         applicability="采集时页面显示 10:30 开始营业及推荐菜；未给更新日期",
         locator="餐厅详情/推荐菜/人均", limitations="第三方信息；推荐菜不等于门店有售证明"),
    dict(source_id="src_kfwt_wall_2022_20261023", file="kaifeng_wall_operator_2022_20261023.json",
         name="开封城墙恢复开放公告（2022）", publisher="开封市文化旅游投资集团", source_type="operator",
         url="https://www.kfwt.group/news/22111218_newsdel.html", published_at="2022-09-09",
         applicability="2022-09-09 恢复开放时规则，仅作历史参照", locator="开放时间/登城口",
         limitations="防疫要求与限流政策已过时；不能当作 2026 当前规则"),
    dict(source_id="src_kf_wall_entrances_20260813", file="kaifeng_wall_entrances_20260813_20261023.json",
         name="开封城墙景区入口在哪?怎么去?", publisher="开封本地宝", source_type="other",
         url="https://kf.bendibao.com/tour/2026813/24535.shtm", published_at="2026-08-13",
         applicability="文章发布日期附近；页面提示以景区最新安排为准",
         locator="一、入口；交通指南；门票", limitations="第三方转述；未给每门精确坐标和现场确认"),
    dict(source_id="src_kf_wall_hours_20260813", file="kaifeng_wall_hours_20260813_20261023.json",
         name="开封城墙开放时间(演出+节目单)", publisher="开封本地宝", source_type="other",
         url="https://kf.bendibao.com/tour/2026813/24537.shtm", published_at="2026-08-13",
         applicability="夏季/冬季分开描述，但未给切换日期；与另一转述冲突",
         locator="一、开放时间；二、演出", limitations="第三方；官网当日公告优先；不是可执行场次"),
    dict(source_id="src_kf_wall_closure_20260104", file="kaifeng_wall_temporary_closure_20260104_20261023.json",
         name="大梁门古马道登城口临时暂停通行", publisher="开封本地宝", source_type="other",
         url="http://kf.bendibao.com/tour/202614/23176.shtm", published_at="2026-01-04",
         applicability="仅 2026-01-05 至 2026-01-09", locator="通知正文",
         limitations="已结束的临时通知；只证明维护机制存在，不证明 2026-10-23 停止使用"),
    dict(source_id="src_qmsyun_q_secret_20261023", file="qmsyun_q_secret_20261023.json",
         name="清明上河园游客中心与服务设施", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/q_secret.htm", published_at=None,
         applicability="官网当前可见；未标更新日期", locator="游客中心/医疗服务/特殊服务",
         limitations="只给相对位置和功能，没有坐标、开放时段与承载量"),
    dict(source_id="src_kfcqjq_weimeng_20261023", file="kfcqjq_weimeng_20261023.json",
         name="微梦大梁门（官网演出页）", publisher="开封市宋都城墙景区", source_type="official",
         url="http://www.kfcqjq.com/www/weimeng/index.html", published_at=None,
         applicability="页面未标更新日期；演出时段不能替代营业规则", locator="演出地点/演出时间/办公地址",
         limitations="未给开园闭园、停止入园、适用日期和入口方向"),
    dict(source_id="src_kaifeng_bus_2022_20261023", file="kaifeng_bus_network_2022_20261023.json",
         name="开封市公共交通2022年各线路运行站点情况", publisher="开封市人民政府", source_type="official",
         url="https://www.kaifeng.gov.cn/kfsrmzfwz/cggjtl/pc/content/content_1897825701877071872.html",
         published_at=None, applicability="2022 年线路资料；不能当作 2026 当前时刻与班次",
         locator="1/8/21/35/53 路等", limitations="只有站序及历史首末班；无实时到站、站点坐标和入口步行路径"),
]

SOURCE_FIELDS = ["source_id", "file", "name", "publisher", "source_type", "url", "published_at", "captured_at", "access_method", "applicability", "locator", "limitations", "sha256"]
manifest = []
for s in SOURCES:
    s = dict(s)
    s["captured_at"] = captured_at(s["file"])
    s["access_method"] = "agent-reach web read --backend exa"
    s["sha256"] = sha(s["file"])
    manifest.append(s)
collection_end = max(s["captured_at"] for s in manifest)
dump("source-manifest.json", {"schema_version": "kaifeng-data-manifest/1", "captured_at": collection_end, "sources": manifest})

scope = [
    dict(batch_id="batch_01", scope_status="candidate", city="开封", anchor_places=["开封站", "开封北站", "清明上河园", "第一楼寺后街店", "开封城墙大梁门古马道登城口"], transport_modes=["walk", "public_bus", "taxi_or_ride_hail"], included_now=["station_arrival_candidates", "one_scenic", "one_restaurant_branch", "one_wall_gate"], deferred=["万岁山", "龙亭", "开封府", "铁塔", "小宋城", "鼓楼夜市"], reason="首批先验证站—景区—餐饮—城墙的一条可核查链；其余目的地延后"),
    dict(batch_id="batch_02", scope_status="planned_not_collected", city="开封", anchor_places=["万岁山", "龙亭", "开封府"], transport_modes=["walk", "public_bus", "taxi_or_ride_hail"], included_now=[], deferred=[], reason="待第一链完成入口/场次/供给核验后再扩"),
    dict(batch_id="batch_03", scope_status="planned_not_collected", city="开封", anchor_places=["铁塔", "小宋城", "鼓楼夜市"], transport_modes=["walk", "public_bus", "taxi_or_ride_hail"], included_now=[], deferred=[], reason="夜间与餐饮节点待目标日期确认后补"),
]
dump("first-batch-scope.json", {"schema_version": "kaifeng-scope/1", "generated_at": NOW, "batches": scope})

places = [
    dict(place_id="kf_station", name="开封站", aliases=["开封火车站"], place_type="transport", address="开封市禹王台区中山路1号（第三方地址，待官方确认）", longitude=None, latitude=None, coordinate_system="unknown", parent_place_id=None, evidence_status="name_and_city_candidate", source_ids=["src_kaifeng_bus_2022_20261023"], missing=["official exact address", "station passenger exit layout", "bus stop pickup point", "verified GCJ02 coordinates"], note="与开封北站分开；公交 2022 年站序含火车站，不等于 2026 班次"),
    dict(place_id="kf_north_station", name="开封北站", aliases=["高铁开封北站"], place_type="transport", address="开封市龙亭区东京大道与四大街交叉口（第三方/百科候选）", longitude=None, latitude=None, coordinate_system="unknown", parent_place_id=None, evidence_status="name_and_city_candidate", source_ids=["src_kaifeng_bus_2022_20261023"], missing=["official exact station exit", "bus stop pickup point", "verified GCJ02 coordinates", "2026 current lines/times"], note="不是开封站；站前广场/综合客运枢纽资料不等于铁路出站口"),
    dict(place_id="qingming", name="清明上河园", aliases=["清园"], place_type="scenic", address="开封市龙亭西路5号（历史包候选，需复核）", longitude=None, latitude=None, coordinate_system="unknown", parent_place_id=None, evidence_status="official_page_candidate", source_ids=["src_qmsyun_program_20261023", "src_qmsyun_architecture_20261023"], missing=["official current address confirmation", "verified boundary/coordinate", "ticket rule for target date"], note="演出节目单页脚给出 09:00 开园、17:30 夜游、22:00 闭园，但个别节目时刻晚于闭园，存在内部矛盾"),
    dict(place_id="qm_gate_yingbin", name="迎宾门", aliases=["东大门"], place_type="entrance", address=None, longitude=None, latitude=None, coordinate_system="unknown", parent_place_id="qingming", evidence_status="official_name_and_relative_position", source_ids=["src_qmsyun_architecture_20261023", "src_qmsyun_gate_channels_20261023"], missing=["gate coordinate", "entry/exit direction on target date", "accepts all ticket types", "field check"], note="官网称东大门；2023 五一攻略称入园通道，只能作季节性候选"),
    dict(place_id="qm_gate_jinshui", name="金水门", aliases=["西门"], place_type="entrance", address=None, longitude=None, latitude=None, coordinate_system="unknown", parent_place_id="qingming", evidence_status="official_name_and_relative_position", source_ids=["src_qmsyun_architecture_20261023", "src_qmsyun_song_banquet_20261023", "src_qmsyun_gate_channels_20261023"], missing=["gate coordinate", "target-date entry/exit direction", "all-ticket acceptance", "field check"], note="官网称对应西门；宋宴页称距金水门最近，可作餐饮接近性候选，不等于入口通行已核验"),
    dict(place_id="qm_gate_duan", name="端门", place_type="entrance", address=None, longitude=None, latitude=None, coordinate_system="unknown", parent_place_id="qingming", evidence_status="official_name_and_seasonal_entry", source_ids=["src_qmsyun_gate_channels_20261023", "src_qmsyun_song_banquet_20261023"], missing=["gate coordinate/relative location", "target-date entry/exit direction", "field check"], note="仅在 2023 五一攻略和当前宋宴问答中出现，不能外推常规开放"),
    dict(place_id="diyilou_sihou", name="第一楼（寺后街店）", aliases=["第一楼灌汤包(寺后街店)", "第一楼·中华老字号(寺后街总店)"], place_type="restaurant", address="开封市寺后街8号", longitude=None, latitude=None, coordinate_system="unknown", parent_place_id=None, evidence_status="official_address_candidate", source_ids=["src_diyilou_home_20261023", "src_qunar_diyilou_20261023"], missing=["verified GCJ02 coordinates", "official current hours", "branch menu", "last order time", "reservation rule"], note="官网给出门店地址和电话；营业与菜品供应来自不同等级来源"),
    dict(place_id="wall_daliang", name="开封城墙大梁门古马道登城口", place_type="entrance", address=None, longitude=None, latitude=None, coordinate_system="unknown", parent_place_id="wall_daliang_scenic", evidence_status="third_party_named_candidate", source_ids=["src_kf_wall_entrances_20260813", "src_kf_wall_closure_20260104"], missing=["official coordinates", "current entry/exit direction", "current hours", "field check"], note="2026-01 临时关闭通知已结束；2026-08 第三方仍列入口，冲突尚未解决；临时替代口往南 170m 只适用于那五天"),
    dict(place_id="wall_daliang_scenic", name="开封城墙（大梁门—安远门开放段）", aliases=["开封城墙景区"], place_type="scenic", address="开封市龙亭区大梁路与内顺城路交叉口（官网办公地址候选）", longitude=None, latitude=None, coordinate_system="unknown", parent_place_id=None, evidence_status="official_office_address_and_third_party_scope", source_ids=["src_kfwt_wall_2022_20261023", "src_kf_wall_entrances_20260813"], missing=["official target-date opening rule", "official entry gate list", "verified coordinates", "ticket/closure status"], note="官网演出时段 09:00-21:00 / 09:15-21:15 未标适用日期；不能替代开园/闭园与停止入园"),
]
dump("places.json", {"schema_version": "kaifeng-places/1", "generated_at": NOW, "places": places})

entrances = [
    dict(entrance_relation_id="ent_qm_yingbin_entry", place_id="qingming", entrance_place_id="qm_gate_yingbin", direction="entry", evidence_status="seasonal_official_candidate", applicable_period="2023 五一假期，具体日期待查", source_ids=["src_qmsyun_gate_channels_20261023"], note="2023 攻略明确列为假期入园通道；不能外推为任意日期开放"),
    dict(entrance_relation_id="ent_qm_jinshui_entry", place_id="qingming", entrance_place_id="qm_gate_jinshui", direction="entry", evidence_status="seasonal_and_product_specific_candidate", applicable_period="2023 五一及宋宴购票问答（后者无发布日期）", source_ids=["src_qmsyun_gate_channels_20261023", "src_qmsyun_song_banquet_20261023"], note="宋宴页只说官方小程序购票后可从此入院；普通门票/其他平台是否适用未确认"),
    dict(entrance_relation_id="ent_qm_duan_entry", place_id="qingming", entrance_place_id="qm_gate_duan", direction="entry", evidence_status="seasonal_and_product_specific_candidate", applicable_period="2023 五一及宋宴购票问答（后者无发布日期）", source_ids=["src_qmsyun_gate_channels_20261023", "src_qmsyun_song_banquet_20261023"], note="端门相对位置、普通门票适用性和出口方向均未确认"),
    dict(entrance_relation_id="ent_wall_daliang_entry", place_id="wall_daliang_scenic", entrance_place_id="wall_daliang", direction="entry", evidence_status="third_party_candidate", applicable_period="2026-08-13 文章（建议以景区最新安排为准）", source_ids=["src_kf_wall_entrances_20260813"], note="只能作为候选入口；未确认当前是否仍可进、是否有临时改道"),
]
dump("entrance-candidates.json", {"schema_version": "kaifeng-entrances/1", "generated_at": NOW, "entrances": entrances})

opening = [
    dict(rule_id="open_qm_page_candidate", place_id="qingming", business_date="2026-10-23", opens_at="2026-10-23T09:00:00+08:00", closes_at="2026-10-23T22:00:00+08:00", night_visit_at="2026-10-23T17:30:00+08:00", last_entry_at=None, reentry="unknown", reservation="unknown", ticket_scope="预约/票种不得由此页面确认", evidence_status="official_page_candidate_with_date_label", source_ids=["src_qmsyun_program_20261023"], conflicts=["节目表出现 22:05/22:30 条目，晚于 22:00 闭园；可能是闭园后答谢或页面错误，不能用于行程"], missing=["target trip date", "last entry", "ticket/booking rule", "event-day closure"]),
    dict(rule_id="open_wall_summer_2026_candidate", place_id="wall_daliang_scenic", business_date="2026-10-23", opens_at="2026-10-23T08:30:00+08:00", closes_at="2026-10-23T22:00:00+08:00", last_entry_at=None, reentry="unknown", reservation="unknown", ticket_scope=None, evidence_status="third_party_seasonal_candidate", source_ids=["src_kf_wall_hours_20260813"], conflicts=["官网只给演出时段 09:00-21:00/09:15-21:15", "2022 运营公告给 08:30-22:00、21:30 停止检票", "2026 第三方入口页给大梁门 08:30-21:30", "另有采集线索称 08:00-22:00 或 21:00 停检，原始证据未保存，不纳入已证据化冲突"], missing=["official 2026-10-23 rule", "season switch date", "last ticket entry", "event closures"]),
]
dump("opening-rule-candidates.json", {"schema_version": "kaifeng-opening/1", "generated_at": NOW, "rules": opening})

program_text = text_of("qmsyun_program_20261023.json")
program_rows = []
for line in program_text.splitlines():
    if not line.startswith("|") or line.startswith("| ---") or "展示名称" in line:
        continue
    cells = [x.strip() for x in line.strip("|").split("|")]
    if len(cells) != 3:
        continue
    name, stage, times = cells
    if not name or name.startswith("演艺部"):
        continue
    starts = [x.strip() for x in times.split(",") if re.fullmatch(r"\d{2}:\d{2}", x.strip())]
    program_rows.append(dict(program_name=name, stage_label=stage, listed_times=times, parsed_start_times=starts, business_date=None, ends_at=None, admission_deadline=None, status="undated_catalog_only", source_id="src_qmsyun_program_20261023", limitation="无目标日期、结束时刻、取消状态；不得导入为 ShowSession"))
dump("program-catalog.json", {"schema_version": "kaifeng-program-catalog/1", "generated_at": NOW, "source_date_label": "2026-10-23", "programs": program_rows, "notice": "此文件只是节目目录，不是某日场次表。东京梦华的演出时间仍为景区公示，不可由本表推导。"})

dates = dict(schema_version="kaifeng-target-date-status/1", generated_at=NOW, target_business_date=None, requested_show=None, status="blocked_missing_target_date", reasons=["用户没有提供实际游玩日期", "节目单没有结束时刻和逐日取消状态", "城墙当前营业规则仍冲突", "餐饮分店供应没有当前菜单证据"], required_inputs=["目标当地日期", "最早到达时间/最晚离开时间", "是否必须看某一场演出", "是否接受两段式用餐或备选餐厅"], prohibited_outputs=["把 2026-10-23 采集日当作游客游玩日", "把常规节目表直接当作目标日 ShowSession", "把品牌菜名当作门店供应"], next_validation=["向景区/商家按目标日期询价与确认", "现场核对入口、出口、演出结束和离园动线", "用带时间戳的截图/录音/票据留证"])
dump("target-date-status.json", dates)

foods = [
    dict(food_id="food_xiaolong_guantangbao", name="小笼灌汤包子", aliases=["灌汤包", "开封小笼包"], brief="薄皮大馅、灌汤流油，连笼上桌；开封传统名吃", ingredients="正文未给完整配料表，不能据名称推断猪肉/清真/无过敏原", dietary_note="未知，须向门店确认；不得推断适合任何忌口", source_ids=["src_diyilou_mingcai_20261023", "src_kaifeng_gov_diyilou_20261023"], evidence_status="official_cultural_and_brand_description"),
    dict(food_id="food_huanghe_liyu_beimian", name="黄河鲤鱼焙面", aliases=["鲤鱼焙面"], brief="第一楼品牌名菜页列出；具体做法/配料待门店菜单确认", ingredients="未知，不能据此推定无过敏原或门店有售", dietary_note="未知", source_ids=["src_diyilou_mingcai_20261023"], evidence_status="official_brand_dish_candidate"),
    dict(food_id="food_tongziji", name="桶子鸡", brief="第一楼品牌名菜页列出；门店供应与配料未确认", ingredients="未知", dietary_note="未知", source_ids=["src_diyilou_mingcai_20261023"], evidence_status="official_brand_dish_candidate"),
    dict(food_id="food_hongshuni", name="红薯泥", brief="第一楼品牌名菜页列出；门店供应与配料未确认", ingredients="未知", dietary_note="未知", source_ids=["src_diyilou_mingcai_20261023"], evidence_status="official_brand_dish_candidate"),
]
dump("foods.json", {"schema_version": "kaifeng-foods/1", "generated_at": NOW, "foods": foods})

vendor = dict(vendor_id="vendor_diyilou_sihou", place_id="diyilou_sihou", name="第一楼（寺后街店）", address="开封市寺后街8号", phone_candidates=["0371-25998655", "0371-25998666", "0371-25998699"], hours_candidates=[dict(value="10:30-14:00,17:30-21:00", source_id="src_qunar_diyilou_20261023", basis="third_party"), dict(value="10:30开始营业", source_id="src_ctrip_diyilou_20261023", basis="third_party_partial"), dict(value="10:00-21:00", source_id="historical_cncn_not_saved", basis="unsaved_third_party_lead_only")], last_order=None, reservation="unknown", average_spend_candidates=[dict(value=60, currency="CNY", basis="ctrip_user_feedback"), dict(value=63, currency="CNY", basis="ctrip_page"), dict(value=64, currency="CNY", basis="ctrip_other_view")], meal_duration=None, queue_time=None, evidence_status="official_address_third_party_operations", source_ids=["src_diyilou_home_20261023", "src_qunar_diyilou_20261023", "src_ctrip_diyilou_20261023"], missing=["current branch menu with photo/date", "last order", "branch-specific reservation", "measured meal duration", "measured queue", "allergen confirmation"], note="同名不同分店分开；不能把街区或品牌页当成本店供应证据")
dump("vendor-candidates.json", {"schema_version": "kaifeng-vendors/1", "generated_at": NOW, "vendors": [vendor]})

supply = [
    dict(supply_id="supply_candidate_diyilou_guantangbao", vendor_id="vendor_diyilou_sihou", food_id="food_xiaolong_guantangbao", evidence_note="品牌官网名菜页列出小笼灌汤包子；官网/政府介绍证明第一楼品牌与该菜关联，但未证明寺后街店目标日期有售", proof_type="brand_level_only", status="candidate_not_supply_proof", source_ids=["src_diyilou_mingcai_20261023", "src_kaifeng_gov_diyilou_20261023"], required_proof=["带日期和门店名的菜单照片", "商家公开菜单链接+门店字段", "现场确认记录"]),
    dict(supply_id="supply_candidate_diyilou_liyu", vendor_id="vendor_diyilou_sihou", food_id="food_huanghe_liyu_beimian", evidence_note="第一楼品牌名菜页列出；标题可识别，但门店供应关系仍未证实", proof_type="brand_level_only", status="candidate_not_supply_proof", source_ids=["src_diyilou_mingcai_20261023"], required_proof=["门店菜单或现场确认"]),
]
dump("supply-candidates.json", {"schema_version": "kaifeng-supply/1", "generated_at": NOW, "supplies": supply, "notice": "所有 supply 记录均未达到供应关系验收；不得标为 verified"})

services = [
    dict(facility_id="service_qm_visitor_center", place_id="qingming", facility_type="visitor_center", location_text="迎宾门南侧、端门北侧、金水门", evidence_status="official_web_candidate", source_ids=["src_qmsyun_q_secret_20261023"], limitation="原始网页已保存为 qmsyun_q_secret_20261023.json；页面只给相对位置，不能给坐标和营业时段"),
    dict(facility_id="service_qm_rest_area", place_id="qingming", facility_type="rest_area", location_text="游客中心有休息室（具体开放/座位未知）", evidence_status="official_web_candidate", source_ids=["src_qmsyun_q_secret_20261023"], limitation="有休息室文字；开放时段、座位数量和位置未确认"),
    dict(facility_id="service_wall_phone", place_id="wall_daliang_scenic", facility_type="information", location_text="景区服务咨询电话 0371-23237131", evidence_status="third_party_closure_notice_lead", source_ids=["src_kf_wall_closure_20260104"], limitation="电话不是厕所/饮水点，需区分信息点与便民设施"),
]
dump("service-facilities.json", {"schema_version": "kaifeng-services/1", "generated_at": NOW, "facilities": services})

transport = [
    dict(route_id="candidate_kf_station_qingming_bus1", from_place_id="kf_station", to_place_id="qingming", mode="public_bus", line_candidate="1路", basis="2022 政府站序：1 路包含火车站和清明上河园相关站序；2024 第三方攻略称约 39 分钟", evidence_status="historical_and_third_party_estimate", source_ids=["src_kaifeng_bus_2022_20261023"], duration_minutes_candidate=39, duration_basis="third_party_estimate_not_measured", applicable_date=None, caveats=["2022 线路不等于 2026 班次", "公交站到具体景区入口步行和安检时间未计", "39 分钟不是高德当前估时，也不是实测"]),
    dict(route_id="candidate_kf_north_qingming_bus53", from_place_id="kf_north_station", to_place_id="qingming", mode="public_bus", line_candidate="53路", basis="2024 第三方攻略称约 58 分钟；2022 政府资料只证明北站公交存在，53 路对应关系需另核", evidence_status="third_party_estimate_line_unconfirmed", source_ids=["src_kaifeng_bus_2022_20261023"], duration_minutes_candidate=58, duration_basis="third_party_estimate_not_measured", applicable_date=None, caveats=["53 路线路/站点、运营日期与下车站均未核实", "不能用于可执行路线"]),
    dict(route_id="candidate_kf_north_qingming_bus8_21_34", from_place_id="kf_north_station", to_place_id="qingming", mode="public_bus", line_candidate="8/21路，换乘34路", basis="2024 第三方攻略的组合候选；本批仅保存到官方 2022 线网存在性", evidence_status="third_party_combination_unverified", source_ids=["src_kaifeng_bus_2022_20261023"], duration_minutes_candidate=None, duration_basis="unknown", applicable_date=None, caveats=["换乘站点、方向、首末班、步行距离均未核验"]),
]
dump("transport-candidates.json", {"schema_version": "kaifeng-transport/1", "generated_at": NOW, "routes": transport, "notice": "城市转场应由系统按目标时间向高德查询，并加入出园/进园/缓冲；本文件只保存候选线路和证据缺口。"})

media = [
    dict(media_id="media_qm_program_text", asset_type="text", title="清明上河园节目表", url="http://www.qmsyun.com/Mobile_page/o_program.php", publisher="清明上河园", rights_status="unknown", usage_decision="link_only", source_id="src_qmsyun_program_20261023", note="可用于内部研究；不得直接把网页图片/文案用于商用宣传"),
    dict(media_id="media_wall_weimeng", asset_type="text", title="微梦大梁门介绍与演出时段", url="http://www.kfcqjq.com/www/weimeng/index.html", publisher="开封市宋都城墙景区", rights_status="unknown", usage_decision="link_only", source_id="src_kfcqjq_weimeng_20261023", note="未保存本批原始响应；演出时段无适用日期，不能当营业规则"),
]
dump("media-assets.json", {"schema_version": "kaifeng-media/1", "generated_at": NOW, "assets": media, "notice": "网页可访问不等于获得图片/导览图商用授权。基础素材需自拍或书面授权。"})

owners = dict(schema_version="kaifeng-maintenance/1", generated_at=NOW, assigned_owner=None, proposed_owner="项目数据维护者（待指定）", assigned=False, update_cadence=[dict(scope="景区/商家营业与票务规则", frequency="每 7 天，出发前 24 小时复核", trigger="公告、节假日、临时闭园"), dict(scope="演出与取消状态", frequency="目标演出日前 24 小时及当天开演前复核", trigger="天气、临时停演、加场"), dict(scope="道路与入口", frequency="每次正式走查前复核", trigger="施工、封路、临时入口调整"), dict(scope="来源有效性与权限", frequency="每月", trigger="版权/页面下线/数据更新")], owner_fields=["姓名/岗位", "复核范围", "有效日期", "复核证据位置"], status="not_assigned", note="没有真实负责人，不能写已验证的更新责任链")
dump("responsibilities.json", owners)

field_rows = []
dump("field-walkthrough.json", {"schema_version": "kaifeng-field-walk/1", "generated_at": NOW, "walks": field_rows, "status": "not_performed", "required_steps": ["记录目标日期和实际到达/离开时刻", "记录车站出站口到公交站/上车点", "记录景区入口安检/换票/步行时间", "记录演出实际开始结束及提前到场", "记录餐厅点单/等餐/用餐/离店时间", "记录临时休息/跳过一站后的调整", "附照片、票据或时间戳证据"], "notice": "正式验收必需；目前没有任何一次完整真实走查"})

coverage = [
    dict(no=1, category="首批服务范围", status="candidate_scope_prepared", evidence="first-batch-scope.json", remaining="需要确定目标日期和是否纳入演出/城墙", formal_acceptance=False),
    dict(no=2, category="地点基本信息", status="candidate_names_addresses_partial", evidence="places.json", remaining="坐标、官方地址、别名和地点边界待核验", formal_acceptance=False),
    dict(no=3, category="实际出入口", status="official_names_third_party_access_candidates", evidence="entrance-candidates.json", remaining="无门点坐标、现场通行/方向证据；车站出口与集合点未确认", formal_acceptance=False),
    dict(no=4, category="开放时间", status="conflicting_unverified_candidates", evidence="opening-rule-candidates.json", remaining="城墙适用季节/停检时间冲突；清明上河园目标日规则待确认", formal_acceptance=False),
    dict(no=5, category="门票与预约规则", status="partial_official_candidates", evidence="source-manifest.json / qmsyun song banquet", remaining="普通票、目标日预约、出园再入、城墙票种未统一确认；不采集个人订单/身份证", formal_acceptance=False),
    dict(no=6, category="园内必要道路", status="not_obtained", evidence="no route geometry or passability evidence in this batch", remaining="入口—目标舞台—出口必要道路未走查，不能作正式导航", formal_acceptance=False),
    dict(no=7, category="演出资料", status="catalog_no_target_date_sessions", evidence="program-catalog.json / target-date-status.json", remaining="缺目标日期、结束时间、舞台精确点位、提前到场与取消状态", formal_acceptance=False),
    dict(no=8, category="菜品介绍", status="brand_level_candidates", evidence="foods.json", remaining="完整食材、过敏原与可靠饮食提示未获得", formal_acceptance=False),
    dict(no=9, category="真实餐馆", status="official_address_third_party_hours", evidence="vendor-candidates.json", remaining="分店营业、最后接单、预约和坐标未由门店确认", formal_acceptance=False),
    dict(no=10, category="餐馆与菜品对应", status="brand_level_only_not_supply_proof", evidence="supply-candidates.json", remaining="缺寺后街店当前菜单/现场确认；不把品牌菜名当供应关系", formal_acceptance=False),
    dict(no=11, category="用餐时间和费用", status="partial_estimates", evidence="vendor-candidates.json", remaining="人均有冲突/口径为第三方；用餐时长和排队未实测", formal_acceptance=False),
    dict(no=12, category="城市转场", status="historical_route_candidates", evidence="transport-candidates.json", remaining="需按目标时间查高德并加出入口/步行/缓冲；线路时效未核验", formal_acceptance=False),
    dict(no=13, category="休息与便民点", status="official_text_no_coordinates", evidence="service-facilities.json / qmsyun_q_secret_20261023.json", remaining="已保存官网文字，但缺位置坐标、开放时段和使用限制", formal_acceptance=False),
    dict(no=14, category="图片与地图素材", status="link_only_rights_unknown", evidence="media-assets.json", remaining="无授权素材；不能以网页可见当商用许可", formal_acceptance=False),
    dict(no=15, category="每条资料的依据", status="manifest_with_hashes_and_limitations", evidence="source-manifest.json", remaining="部分线索未保存原文；核验人/有效日期仍缺", formal_acceptance=False),
    dict(no=16, category="更新负责人", status="proposed_not_assigned", evidence="responsibilities.json", remaining="需指定真实负责人并承担复核", formal_acceptance=False),
    dict(no=17, category="真实走查记录", status="not_performed", evidence="field-walkthrough.json", remaining="正式验收必需；当前 0 次完整走查", formal_acceptance=False),
]
write_csv("coverage-17.csv", coverage, ["no", "category", "status", "evidence", "remaining", "formal_acceptance"])
dump("coverage-17.json", {"schema_version": "kaifeng-coverage/1", "generated_at": NOW, "categories": coverage, "overall_status": "candidate_catalog_only_not_executable"})

report = f"""# 开封首批资料候选包（2026-10-23）

## 结论

本批已整理 {len(manifest)} 份来源记录、{len(places)} 个地点候选、{len(entrances)} 条入口关系候选、{len(program_rows)} 条节目目录和 {len(coverage)} 类数据覆盖表。它们是**未核验候选**，不是正式业务事实，也没有任何一条可直接用于可执行路线。

最重要的阻塞是：用户尚未给出目标游玩日期。节目单上的“2026-10-23 更新”是采编日期标签，不是游客游玩日；因此不能生成目标日 `ShowSession`，也不能声明能赶上某场演出。

## 已拿到什么

- **清明上河园**：节目单页脚记录开园 09:00、夜游 17:30、闭园 22:00；但节目表有 22:05/22:30 条目，页面内部存在时间矛盾。迎宾门/金水门/端门只有名称、相对方位和季节性入园证据，没有门点坐标或当前通行确认。
- **宋宴**：官方问答给出 90 分钟、午宴 12:00-13:30、晚宴 18:00-19:30、建议提前 1 小时换手环，以及官方小程序购票后的三个入园门。该页未标发布日期，也没有给出目标日履约证明。
- **第一楼寺后街店**：官网确认寺后街 8 号和门店电话；品牌名菜页列出灌汤包、鲤鱼焙面等。营业时间第三方不一致（去哪儿 10:30-14:00/17:30-21:00；携程只确认 10:30 开始；旧线索另有 10:00-21:00）。品牌菜名不能变成“本店今日有售”。
- **开封城墙**：2022 运营方公告为 08:30-22:00、21:30 停检；2026-08 第三方入口页给大梁门 08:30-21:30，且天波门、安远门为入口、金耀门只出不进；2026-08 时间页又给夏季 08:30-22:00、冬季 08:30-21:30。官网演出页只给 09:00-21:00/09:15-21:15，不能替代营业规则。冲突保留，未强行统一。
- **交通与车站**：政府 2022 年线网能证明部分线路存在，不等于 2026 班次；开封站、开封北站的真实出站口、集合点、公交站步行路径未确认。高德候选项/站点中心点都不作为已核验入口。

## 17 类覆盖

详见 `structured/coverage-17.csv`。当前 17 类全部 `formal_acceptance=false`；其中园内必要道路、真实走查为未取得，负责人为待指定。

## 下一步

1. 给出目标日期、必须看的演出和可接受的用餐时间。
2. 由景区/门店按目标日确认入口、开闭园/停检、场次结束和菜单供应。
3. 现场走一遍车站—公交—景区门—舞台—餐厅—城墙门，记录实际耗时和临时调整。
4. 核验通过后再升 `validation_status`，当前不得导入运行库。
"""
(REPORTS / "batch-report-20261023.md").write_text(report)
print(json.dumps(dict(sources=len(manifest), places=len(places), entrances=len(entrances), programs=len(program_rows), coverage=len(coverage)), ensure_ascii=False))
