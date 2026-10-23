#!/usr/bin/env python3
"""Build the expanded, explicitly non-executable Kaifeng evidence pack."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
STRUCT = ROOT / "structured"
REPORTS = ROOT / "reports"
BASE = ROOT.parent / "collection-20261023"
TZ = ZoneInfo("Asia/Shanghai")


def load(path: Path):
    return json.loads(path.read_text())


def raw_text(name: str) -> str:
    data = load(RAW / name)
    return data["items"][0].get("text") or ""


def source_item(name: str, needle: str) -> dict:
    data = load(RAW / name)
    for item in data.get("items", []):
        if needle in (item.get("url") or "") or needle in (item.get("title") or ""):
            return item
    raise KeyError((name, needle))


def source_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    return (RAW / path if len(path.parts) == 1 else ROOT / path).resolve()


def capture(name: str) -> str:
    return datetime.fromtimestamp(source_path(name).stat().st_mtime, TZ).isoformat(timespec="seconds")


def sha(name: str) -> str:
    return hashlib.sha256(source_path(name).read_bytes()).hexdigest()


def dump(name: str, obj) -> None:
    (STRUCT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def csv_write(name: str, rows: list[dict], fields: list[str]) -> None:
    with (STRUCT / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


NOW = datetime.now(TZ).isoformat(timespec="seconds")
SOURCE_SPECS = [
    dict(file="search_a_level_ticket_map_20261023.json", source_id="src_search_a_level_ticket_map_20261023",
         name="开封 A 级景区名录/门票/旅游地图搜索结果", publisher="搜索摘要（原始发布者见页面 URL）",
         source_type="other", url="search://A级景区/门票/旅游地图", published_at=None,
         locator="A级名录条目、各景区门票/开放时间摘录", applicability="搜索结果正文；A级名录条目与 2026 页面摘录混合，不能逐字当原文",
         limitations="搜索返回的正文可能被拼接/截断；官方名录直读为 403，无法用它确认官网当前完整名录"),
    dict(file="read_a_level_waf_20261023.json", source_id="src_read_a_level_waf_20261023",
         name="开封市 A 级旅游景区名录（直读 WAF 403）", publisher="开封市文化广电和旅游局",
         source_type="official", url="https://wgl.kaifeng.gov.cn/kfswhgdhlyj/swgljlyjq/pc/content/content_1805439358781935616.html",
         published_at=None, locator="403 页面", applicability="仅证明直读被 WAF 阻断，不证明页面内容不可用",
         limitations="无正文；A级名单只能来自搜索结果正文候选"),
    dict(file="read_tickets_20260917_20261023.json", source_id="src_read_tickets_20260917_20261023",
         name="开封景区门票预约方式汇总", publisher="开封本地宝", source_type="other",
         url="http://kf.bendibao.com/tour/2026917/24791.shtm", published_at="2026-09-17",
         locator="各景区预约方式/票种/门票福利", applicability="2026-09-17 发布；作为收费/预约候选，不代替目标日景区公告",
         limitations="第三方汇总；价格、票种与节假日/年卡政策仍需按目标日复核；页面含大量 Base64 噪声"),
    dict(file="read_tickets_20260917_clean_20261023.txt", source_id="src_read_tickets_20260917_clean_20261023",
         name="门票汇总页正文清理文本", publisher="本地整理自开封本地宝", source_type="other",
         url="http://kf.bendibao.com/tour/2026917/24791.shtm", published_at="2026-09-17",
         locator="去除 Base64 噪声后的正文", applicability="用于审阅同一原始页，不是独立来源",
         limitations="派生文件；来源等级与原始页面相同"),
    dict(file="read_tourism_map_20261023.json", source_id="src_read_tourism_map_20261023",
         name="2026 开封旅游地图", publisher="开封本地宝", source_type="other",
         url="http://kf.bendibao.com/tour/lvyouditu/", published_at=None,
         locator="各景点门票与开放时间摘录", applicability="采集时页面；部分条目没有紧随其后的景点标题，不能强行归属",
         limitations="页面条目顺序有截断，营业规则只作线索；与官方页面冲突时不能合并"),
    dict(file="read_kaifengfu_guide_20261023.json", source_id="src_read_kaifengfu_guide_20261023",
         name="开封府参观指南", publisher="开封府", source_type="operator",
         url="http://www.kaifengfu.cn/zhinan/", published_at=None,
         locator="开放时间、票价、游客须知、节目单、联系方式", applicability="官网当前可见；页面内部时间互相矛盾，且未给目标日期",
         limitations="同一页面同时写 07:30-22:00 与夏季 07:00-19:00/冬季 07:30-18:30；节目只有起始时刻"),
    dict(file="search_longting_20261023.json", source_id="src_search_longting_20261023",
         name="龙亭景区搜索证据", publisher="搜索摘要（官方/第三方混合）", source_type="other",
         url="search://龙亭景区", published_at=None, locator="官网游园指南与第三方开放/票务条目",
         applicability="官网 2022 页面与采集日第三方条目并存，不能拼成当前单规则", limitations="搜索摘要；官网原文未单独读取"),
    dict(file="search_tieta_20261023.json", source_id="src_search_tieta_20261023",
         name="铁塔景区搜索证据", publisher="搜索摘要（官方/第三方混合）", source_type="other",
         url="search://铁塔景区", published_at=None, locator="携程/本地宝/省旅游资讯条目",
         applicability="同页多时段并存；只能记录冲突", limitations="搜索摘要；目标日开放时间未确认"),
    dict(file="search_wansuishan_20261023.json", source_id="src_search_wansuishan_20261023",
         name="万岁山搜索证据", publisher="搜索摘要（官网/政府/第三方混合）", source_type="other",
         url="search://万岁山武侠城", published_at=None, locator="票务、简介、营业时间条目",
         applicability="用于找到官方票务和第三方冲突，不能替代官方当前公告", limitations="搜索正文是候选；不把第三方 21:30 停检写成官网规则"),
    dict(file="search_wansuishan_ticket_20261023.json", source_id="src_search_wansuishan_ticket_20261023",
         name="万岁山官方票务搜索结果", publisher="万岁山武侠城", source_type="official",
         url="http://wansuishan.com.cn/mpyd.html", published_at=None, locator="全价票、3日有效、预约剧目、08:00-22:00",
         applicability="官网票务页当前可见；未给目标日适用性", limitations="页面没有明确指出馆/停检和入口；搜索正文不代替完整页面"),
    dict(file="read_wansuishan_ticket_20261023.json", source_id="src_read_wansuishan_ticket_20261023",
         name="万岁山票务直读响应", publisher="万岁山武侠城", source_type="official",
         url="http://wansuishan.com.cn/mpyd.html", published_at=None, locator="Jina 直读结果",
         applicability="直读只返回 URL 源，无可解析正文；因此以搜索正文候选为准", limitations="直读失败；保留失败证据，避免假装原文已核验"),
    dict(file="read_wansuishan_gov_20261023.json", source_id="src_read_wansuishan_gov_20261023",
         name="万岁山武侠城景区简介", publisher="龙亭区人民政府", source_type="official",
         url="http://www.longting.gov.cn/ltq/c00044/pc/content/content_2003660780842905600.html",
         published_at="2025-12-21", locator="景区定位/演出类型/夜游",
         applicability="政府简介；不是票务或当日营业公告", limitations="没有入口、营业时刻、演出结束和取消状态"),
    dict(file="read_xiaosong_ctrip_20261023.json", source_id="src_read_xiaosong_ctrip_20261023",
         name="汴梁小宋城（携程）", publisher="携程", source_type="other",
         url="https://gs.ctrip.com/html5/you/sight/kaifeng165/5067026.html", published_at=None,
         locator="开放时间/地址/点评", applicability="采集时页面；未给发布日期", limitations="第三方；与去哪儿/其他页的 23:00/23:30 说法冲突"),
    dict(file="search_xiaosong_20261023.json", source_id="src_search_xiaosong_20261023",
         name="汴梁小宋城搜索证据", publisher="搜索摘要（携程/去哪儿/高德）", source_type="other",
         url="search://汴梁小宋城", published_at=None, locator="地址、开放时间、免费/演出线索",
         applicability="只能作为候选；高德坐标是地图 POI，不是入口坐标", limitations="第三方坐标和营业时刻未取得运营方确认"),
    dict(file="read_gulou_official_20261023.json", source_id="src_read_gulou_official_20261023",
         name="开封市鼓楼夜市", publisher="鼓楼区人民政府", source_type="official",
         url="http://www.gulou.gov.cn/kfsglqwz/c00173/pc/content/content_1889528282980225024.html",
         published_at="2019-10-31", locator="正规夜市/统一入市撤市/摊位数",
         applicability="2019 页面证明管理机制，不是 2026 目标日营业时刻", limitations="没有稳定开市/撤市时刻；不据此写 10:00-22:00"),
    dict(file="search_night_market_20261023.json", source_id="src_search_night_market_20261023",
         name="开封夜市搜索证据", publisher="搜索摘要（政府/新闻/平台）", source_type="other",
         url="search://开封夜市", published_at=None, locator="鼓楼/西司/河大夜市、临时休市、第三方营业",
         applicability="包含 2026-06 高考休市历史通知和平台营业线索", limitations="平台 10:00-22:00 不是官方常态；临时休市已结束，不能外推今天"),
    dict(file="search_qingming_gates_20261023.json", source_id="src_search_qingming_gates_20261023",
         name="清明上河园入口/票务搜索证据", publisher="搜索摘要（官方公众号/第三方）", source_type="other",
         url="search://清明上河园入口", published_at=None, locator="三入口、实名制、节假日票效、停车/入口攻略",
         applicability="官方公众号节选与第三方入口攻略混合，必须逐条标适用期", limitations="未得到账号文章发布日期；不把节假日票效外推到目标日"),
    dict(file="read_qingming_home_20261023.json", source_id="src_read_qingming_home_20261023",
         name="清明上河园首页直读", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/", published_at=None, locator="首页",
         applicability="只拿到图片和备案号，不能补规则", limitations="无可解析营业/入口/票务正文"),
    dict(file="search_shanshangan_20261023.json", source_id="src_search_shanshangan_20261023",
         name="山陕甘会馆搜索证据", publisher="搜索摘要（携程/省旅游资讯）", source_type="other",
         url="search://山陕甘会馆", published_at=None, locator="开放时间/地址/电话/票价",
         applicability="第三方与旧省局页面并存；不能合并成当前规则", limitations="没有运营方当前官网原文"),
    dict(file="search_baogongci_20261023.json", source_id="src_search_baogongci_20261023",
         name="包公祠搜索证据", publisher="搜索摘要（本地宝/携程）", source_type="other",
         url="search://包公祠", published_at=None, locator="开放时间/票价/交通",
         applicability="2026 第三方汇总；没有景区官网确认", limitations="夏季 18:30/22:00、冬季 17:30 等说法不一致"),
    dict(file="search_yanqingguan_20261023.json", source_id="src_search_yanqingguan_20261023",
         name="延庆观搜索证据", publisher="搜索摘要（携程/本地宝/省旅游资讯）", source_type="other",
         url="search://延庆观", published_at=None, locator="开放时间/票价/地址/电话",
         applicability="第三方候选；季节切换日期与停检时刻存在两套写法", limitations="无运营方官网"),
    dict(file="search_daxiangguosi_20261023.json", source_id="src_search_daxiangguosi_20261023",
         name="大相国寺搜索证据", publisher="搜索摘要（本地宝/携程/省旅游资讯）", source_type="other",
         url="search://大相国寺", published_at=None, locator="开放时间/票价/地址/电话",
         applicability="第三方候选；未取得官网", limitations="夏令/冬季日期与停检时间说法有差异"),
    dict(file="search_tianboyangfu_20261023.json", source_id="src_search_tianboyangfu_20261023",
         name="天波杨府搜索证据", publisher="搜索摘要（本地宝/携程/百科）", source_type="other",
         url="search://天波杨府", published_at=None, locator="开放时间/票价/交通/入口",
         applicability="第三方候选，无官网正文", limitations="日游/夜游切换日期不明；停车与入口未现场确认"),
    dict(file="search_hanyuan_20261023.json", source_id="src_search_hanyuan_20261023",
         name="中国翰园碑林搜索证据", publisher="搜索摘要（官网/携程/高德）", source_type="other",
         url="search://中国翰园碑林", published_at=None, locator="开放时间/地址/南北门",
         applicability="官网与第三方并存；只记录候选", limitations="官网未给目标日/入口适用性；2024 夜游暂停不等于现在仍暂停"),
    dict(file="search_lesser_a_levels_20261023.json", source_id="src_search_lesser_a_levels_20261023",
         name="低等级 A 级景区搜索证据", publisher="搜索摘要（百科/票务/旅游资讯）", source_type="other",
         url="search://禹王台 岳飞庙 刘少奇陈列馆", published_at=None,
         locator="名录、票价线索、地址线索", applicability="只做清单补位与缺口标记", limitations="多数无近期运营方页面，不能给当前时刻/入口"),
    dict(file="search_huangjia_20261023.json", source_id="src_search_huangjia_20261023",
         name="黄家老店搜索证据", publisher="搜索摘要（携程/去哪儿/Trip/地图）", source_type="other",
         url="search://黄家老店", published_at=None, locator="分店地址/营业/人均/推荐菜",
         applicability="具体分店候选；平台菜单/推荐菜不是供应证明", limitations="同名多店，必须按分店分别记录"),
    dict(file="search_xingjia_20261023.json", source_id="src_search_xingjia_20261023",
         name="邢家锅贴搜索证据", publisher="搜索摘要（携程/去哪儿/Trip/高德）", source_type="other",
         url="search://邢家锅贴", published_at=None, locator="大梁门总店地址/营业/人均/推荐菜",
         applicability="具体总店候选；平台推荐菜不是当前菜单", limitations="另一个金明广场店的高德页有已关店评论，不能混用"),
    dict(file="search_louzhonglou_20261023.json", source_id="src_search_louzhonglou_20261023",
         name="楼中楼酒店（小宋城店）搜索证据", publisher="搜索摘要（携程/大众点评）", source_type="other",
         url="search://楼中楼酒店", published_at=None, locator="地址/营业/人均/推荐菜",
         applicability="商场内具体餐饮地点候选；不是小宋城整体营业规则", limitations="平台推荐菜不等于门店供应证明；未取得商家菜单"),
    dict(file="search_heyuan_20261023.json", source_id="src_search_heyuan_20261023",
         name="宋都禾源/禾源灌汤包子搜索证据", publisher="搜索摘要（携程/去哪儿/高德/点评）", source_type="other",
         url="search://宋都禾源", published_at=None, locator="汉兴路/金祥路/鼎立等分店",
         applicability="分店候选；名称和地址可能各有别，不能合并", limitations="第三方平台时间/价格不一致；未得到当前门店菜单"),
    dict(file="search_food_siwei_20261023.json", source_id="src_search_food_siwei_20261023",
         name="四味菜资料搜索证据", publisher="搜索摘要（河南省文旅厅/地方标准/省旅游资讯）", source_type="other",
         url="search://四味菜", published_at=None, locator="食材组成/早餐语境/地方标准",
         applicability="菜品资料可用；具体门店供应仍需菜单", limitations="不同来源的“四味”配方不完全相同，不能强制统一"),
    dict(file="search_food_kaifeng_snacks_20261023.json", source_id="src_search_food_kaifeng_snacks_20261023",
         name="开封小吃资料搜索证据", publisher="搜索摘要（河南省文旅厅/政府/媒体）", source_type="other",
         url="search://开封小吃", published_at=None, locator="炒凉粉/桶子鸡/羊肉炕馍/菜品",
         applicability="菜品文化介绍；未证明任何具体店铺目标日有售", limitations="不要将平台推荐菜或媒体举例当门店供应关系"),
    dict(file="search_shanshangan_officiallead_20261023.json", source_id="src_search_shanshangan_officiallead_20261023",
         name="山陕甘会馆官方线索搜索", publisher="搜索摘要", source_type="other",
         url="search://山陕甘会馆官网", published_at=None, locator="官网未命中，第三方候选",
         applicability="只用于说明未找到运营方当前页面", limitations="‘未找到’不等于不存在"),
    dict(file="search_daxiangguosi_officiallead_20261023.json", source_id="src_search_daxiangguosi_officiallead_20261023",
         name="大相国寺官方线索搜索", publisher="搜索摘要", source_type="other",
         url="search://大相国寺官网", published_at=None, locator="未取得官网正文本",
         applicability="仅用于查找线索", limitations="第三方页面不能替代官方当前公告"),
    dict(file="search_baogongci_officiallead_20261023.json", source_id="src_search_baogongci_officiallead_20261023",
         name="包公祠官方线索搜索", publisher="搜索摘要", source_type="other",
         url="search://包公祠官网", published_at=None, locator="未取得官网正文本",
         applicability="仅用于查找线索", limitations="旧恢复开放公告不等于当前规则"),
    dict(file="search_tianboyangfu_officiallead_20261023.json", source_id="src_search_tianboyangfu_officiallead_20261023",
         name="天波杨府官方线索搜索", publisher="搜索摘要", source_type="other",
         url="search://天波杨府官网", published_at=None, locator="第三方候选",
         applicability="只用于说明未取得运营方原文", limitations="不得将第三方页面称为官网"),
    dict(file="read_food_quality_waf_20261023.json", source_id="src_read_food_quality_waf_20261023",
         name="开封餐饮夜市提质页面（WAF 403）", publisher="开封市人民政府", source_type="official",
         url="https://www.kaifeng.gov.cn/kfsrmzfwz/bmdt/pc/content/content_1815311166039592960.html",
         published_at=None, locator="403", applicability="直读失败，未采用正文",
         limitations="搜索摘要可提到餐饮提质行动，但直读无法确认细节；保持缺口"),
    dict(file="../collection-20261023/raw/qmsyun_program_20261023.json", source_id="src_qmsyun_program_20261023",
         name="清明上河园演出节目单", publisher="清明上河园", source_type="official",
         url="http://www.qmsyun.com/Mobile_page/o_program.php", published_at=None,
         locator="节目名称、舞台和页面时刻", applicability="首批官网页面；未给目标日期、结束时刻和取消状态",
         limitations="只有采集时目录，不得当作指定日期演出成立"),
    dict(file="../collection-20261023/raw/qmsyun_perform_20261023.json", source_id="src_qmsyun_perform_20261023",
         name="清明上河园情景展示片段", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/o_perform.php", published_at=None,
         locator="节目、舞台和时刻", applicability="首批官网页面；时刻未绑定目标日期",
         limitations="不证明具体目标日场次、结束或取消状态"),
    dict(file="../collection-20261023/raw/qmsyun_q_secret_20261023.json", source_id="src_qmsyun_q_secret_20261023",
         name="清明上河园游客中心与服务设施", publisher="清明上河园", source_type="official",
         url="https://www.qmsyun.com/Mobile_page/q_secret.htm", published_at=None,
         locator="游客中心、医疗点、餐饮和联系方式的文字位置", applicability="仅文字相对位置",
         limitations="无坐标、开放时段、通行限制与现场确认"),
    dict(file="../collection-20261023/raw/kfcqjq_weimeng_20261023.json", source_id="src_kfcqjq_weimeng_20261023",
         name="微梦大梁门（官网演出页）", publisher="开封市宋都城墙景区", source_type="official",
         url="http://www.kfcqjq.com/www/weimeng/index.html", published_at=None,
         locator="演出地点与时段", applicability="首批官网页面；不是城墙营业规则",
         limitations="未给适用日期、闭园/停检、入口与取消状态"),
    dict(file="../collection-20261023/raw/kaifeng_wall_entrances_20260813_20261023.json",
         source_id="src_kf_wall_entrances_20260813", name="开封城墙景区入口在哪?怎么去?",
         publisher="开封本地宝", source_type="other",
         url="https://kf.bendibao.com/tour/2026813/24535.shtm", published_at="2026-08-13",
         locator="入口与交通文字", applicability="第三方转述",
         limitations="无门点坐标、目标日通行和现场确认"),
    dict(file="../collection-20261023/raw/kaifeng_wall_hours_20260813_20261023.json",
         source_id="src_kf_wall_hours_20260813", name="开封城墙开放时间(演出+节目单)",
         publisher="开封本地宝", source_type="other",
         url="https://kf.bendibao.com/tour/2026813/24537.shtm", published_at="2026-08-13",
         locator="开放与演出时间", applicability="第三方转述",
         limitations="与运营方/其他页面冲突，不能当目标日规则"),
    dict(file="../collection-20261023/raw/kaifeng_wall_operator_2022_20261023.json",
         source_id="src_kfwt_wall_2022_20261023", name="开封城墙恢复开放公告（2022）",
         publisher="开封市文化旅游投资集团", source_type="operator",
         url="https://www.kfwt.group/news/22111218_newsdel.html", published_at="2022-09-09",
         locator="2022 年开放时间与登城口", applicability="历史运营公告",
         limitations="不能作为 2026 目标日规则"),
    dict(file="../collection-20261023/raw/kaifeng_wall_temporary_closure_20260104_20261023.json",
         source_id="src_kf_wall_closure_20260104", name="大梁门古马道登城口临时暂停通行",
         publisher="开封本地宝", source_type="other",
         url="http://kf.bendibao.com/tour/202614/23176.shtm", published_at="2026-01-04",
         locator="临时暂停通行通知", applicability="仅 2026-01-05 至 2026-01-09",
         limitations="已结束的临时通知，不证明当前通行状态"),
]
sources = []
for spec in SOURCE_SPECS:
    spec = dict(spec)
    if not spec["file"].startswith("../") and "/" not in spec["file"]:
        spec["file"] = f"raw/{spec['file']}"
    spec["captured_at"] = capture(spec["file"])
    spec["file_sha256"] = sha(spec["file"])
    spec["access_method"] = "agent-reach web search/read (exa/jina/public web); local capture"
    sources.append(spec)
sources.append({
    "source_id": "src_first_batch_manifest_20261023", "file": "../collection-20261023/MANIFEST.sha256",
    "name": "首批候选包清单", "publisher": "本项目首批采集", "source_type": "other",
    "url": None, "published_at": None, "captured_at": datetime.fromtimestamp((BASE / "MANIFEST.sha256").stat().st_mtime, TZ).isoformat(timespec="seconds"),
    "access_method": "local SHA256 manifest", "applicability": "仅核对范围/证据等级，不复用为新的开源事实",
    "locator": "MANIFEST.sha256", "limitations": "首批包含采集日与 target date 混淆的开放候选，扩展包不继承该日期语义",
    "file_sha256": hashlib.sha256((BASE / "MANIFEST.sha256").read_bytes()).hexdigest(),
})
# Reuse the first batch's first-party and platform captures by explicit identity
# rather than assigning an unrelated expanded-pack source.
base_sources = load(BASE / "structured" / "source-manifest.json")["sources"]
reused_source_ids = {
    "src_diyilou_home_20261023",
    "src_diyilou_mingcai_20261023",
    "src_kaifeng_gov_diyilou_20261023",
    "src_qunar_diyilou_20261023",
    "src_ctrip_diyilou_20261023",
    "src_kaifeng_bus_2022_20261023",
    "src_qmsyun_architecture_20261023",
    "src_qmsyun_gate_channels_20261023",
    "src_qmsyun_song_banquet_20261023",
    "src_qmsyun_tokyo_dream_20261023",
}
for base_source in base_sources:
    if base_source["source_id"] not in reused_source_ids:
        continue
    spec = {
        "source_id": base_source["source_id"],
        "file": f"../collection-20261023/raw/{base_source['file']}",
        "name": base_source["name"],
        "publisher": base_source["publisher"],
        "source_type": base_source["source_type"],
        "url": base_source["url"],
        "published_at": base_source.get("published_at"),
        "captured_at": base_source["captured_at"],
        "access_method": base_source["access_method"],
        "applicability": base_source["applicability"],
        "locator": base_source["locator"],
        "limitations": base_source["limitations"],
        "file_sha256": base_source["sha256"],
        "reused_from": "collection-20261023",
    }
    sources.append(spec)
dump("source-manifest-expanded.json", {"schema_version": "kaifeng-expanded-manifest/1", "generated_at": NOW,
                                        "collection_date": "2026-10-23", "sources": sources,
                                        "notice": "搜索摘要、直读失败、派生文本均作为证据保留；不把所有候选升为 verified。"})

# A-level list from the search result body; never promoted to a current official directory.
# The body is a three-line row: sequence number, name, level.
a_text = source_item("search_a_level_ticket_map_20261023.json", "A级旅游景区名录")["text"]
a_lines = [line.strip() for line in a_text.splitlines() if line.strip()]
a_rows = []
for index, line in enumerate(a_lines[:-2]):
    seq_text = line.rstrip(".")
    level = a_lines[index + 2]
    if not seq_text.isdigit() or level not in ("5A", "4A", "3A", "2A"):
        continue
    name = a_lines[index + 1].replace("张 庄", "张庄").replace("万岁山大宋武侠城", "万岁山武侠城")
    if not name or name in ("序号", "名称", "级别"):
        continue
    a_rows.append({"seq": int(seq_text), "name": name, "level": level,
                   "source_id": "src_search_a_level_ticket_map_20261023",
                   "status": "directory_search_candidate", "official_page_read": False})
if len(a_rows) != 24 or [row["seq"] for row in a_rows] != list(range(1, 25)):
    raise ValueError(f"A级名录解析异常: count={len(a_rows)}, seq={[row['seq'] for row in a_rows]}")
dump("a-level-directory-expanded.json", {"schema_version": "kaifeng-a-level-directory/1", "generated_at": NOW,
                                         "count": len(a_rows), "places": a_rows,
                                         "notice": "来自 A 级名录的搜索正文候选。官方页直读 403，故不是已核验的当前官方名录。"})

place_specs = [
    # id, name, aliases, type, address, level/source status, known source ids, missing/focus
    ("qingming", "清明上河园", ["清园"], "scenic", "开封市龙亭区龙亭西路5号（搜索候选）", "5A", ["src_search_a_level_ticket_map_20261023", "src_search_qingming_gates_20261023"], ["目标日票效与停止入园", "门点坐标/全部入口通行", "目标日《东京梦华》结束时刻", "园内主路与现场走查"]),
    ("kaifengfu", "开封府", ["开封府景区"], "scenic", "河南省开封市鼓楼区包公东湖北岸（官网）", "4A", ["src_read_kaifengfu_guide_20261023", "src_search_a_level_ticket_map_20261023"], ["同一官网页的营业时间冲突", "目标日节目完整场次", "游客入口/出口"]),
    ("baogongci", "包公祠", [], "scenic", "开封市鼓楼区向阳路1号（第三方汇总）", "4A", ["src_search_baogongci_20261023", "src_search_a_level_ticket_map_20261023"], ["运营方当前开放规则", "夜游适用期", "真实入口", "预约/票种"]),
    ("longting", "龙亭景区", ["龙亭公园"], "scenic", "开封市宋都御街北段（官网）", "4A", ["src_search_longting_20261023", "src_search_a_level_ticket_map_20261023"], ["2022 官网指南与 2026 第三方冲突", "南门/北门目标日通行", "夜游规则"]),
    ("tieta", "铁塔景区", ["铁塔公园"], "scenic", "开封市顺河回族区北门大街210号（第三方）", "4A", ["src_search_tieta_20261023", "src_search_a_level_ticket_map_20261023"], ["08:30-18:00 与 08:30-21:00 冲突", "目标日停止入园", "入口坐标"]),
    ("daxiangguosi", "大相国寺", [], "scenic", "开封市鼓楼区自由路西段36号（第三方）", "4A", ["src_search_daxiangguosi_20261023", "src_search_a_level_ticket_map_20261023"], ["官网原文未取得", "夏令/冬季边界", "入寺入口与礼仪限制"]),
    ("hanyuan", "中国翰园碑林", ["翰园碑林"], "scenic", "开封市龙亭区龙亭北路15号（官网摘要）", "4A", ["src_search_hanyuan_20261023", "src_search_a_level_ticket_map_20261023"], ["官网 22:00 与平台 21:00 停入冲突", "南北门目标日开放", "夜游是否恢复"]),
    ("qifeng", "启封故园", [], "scenic", "开封市祥符区朱仙镇（待核）", "4A", ["src_search_a_level_ticket_map_20261023"], ["目标日营业", "地址/入口", "交通", "节目"]),
    ("wansuishan", "万岁山武侠城", ["万岁山", "万岁山大宋武侠城"], "scenic", "开封市龙亭区东京大道中段（官网摘要）", "4A", ["src_search_wansuishan_ticket_20261023", "src_read_wansuishan_gov_20261023", "src_search_a_level_ticket_map_20261023"], ["官网 08:00-22:00 vs 第三方 21:00/21:30 停检", "东/西/中门目标日通行", "预约剧目的目标日场次"]),
    ("jiaoyulu", "焦裕禄纪念园", [], "scenic", "兰考县（待核）", "4A", ["src_search_a_level_ticket_map_20261023"], ["地址/开放/预约", "入口", "现场走查"]),
    ("tianboyangfu", "天波杨府", [], "scenic", "开封市龙亭区龙亭北路14号（第三方）", "4A", ["src_search_tianboyangfu_20261023", "src_search_a_level_ticket_map_20261023"], ["日常/夜游切换边界", "20:30 vs 18:30", "停车/入口"]),
    ("aisijia", "爱思嘉·农业嘉年华", [], "scenic", "开封市祥符区（待核）", "4A", ["src_search_a_level_ticket_map_20261023"], ["目标日营业/票价", "入口", "公共交通"]),
    ("huangsongdaguan", "皇宋大观", [], "scenic", None, "3A", ["src_search_a_level_ticket_map_20261023"], ["运营状态", "地址/入口", "目标日规则"]),
    ("yunuo", "雨诺生态园", [], "scenic", None, "3A", ["src_search_a_level_ticket_map_20261023"], ["运营状态", "地址/入口", "目标日规则"]),
    ("xiaosongcheng", "汴梁小宋城", [], "scenic", "开封市龙亭区大梁路208号（第三方）", "3A", ["src_search_xiaosong_20261023", "src_read_xiaosong_ctrip_20261023"], ["运营方当前营业时刻", "23:00/23:30/全天开放冲突", "入口与演出票规则"]),
    ("yudian", "余店乡村旅游度假区", [], "scenic", None, "3A", ["src_search_a_level_ticket_map_20261023"], ["地址/入口", "目标日营业", "交通"]),
    ("lankaowenhua", "兰考县文化交流中心", [], "scenic", None, "3A", ["src_search_a_level_ticket_map_20261023"], ["地址/入口", "预约/开放", "活动安排"]),
    ("wall", "开封城墙", ["开封城墙景区"], "scenic", "开封市龙亭区大梁路与内顺城路交叉口（首批候选）", "3A", ["src_search_a_level_ticket_map_20261023", "src_read_tourism_map_20261023"], ["运营方目标日规则", "开放段/登城口", "联票权益范围"]),
    ("yuwangtai", "禹王台景区", [], "scenic", None, "2A", ["src_search_a_level_ticket_map_20261023", "src_search_lesser_a_levels_20261023"], ["运营状态/票价", "地址/入口", "目标日规则"]),
    ("shanshangan", "山陕甘会馆", [], "scenic", "开封市龙亭区徐府街85号（第三方）", "2A", ["src_search_shanshangan_20261023", "src_search_a_level_ticket_map_20261023"], ["运营方当前开放规则", "夏令/冬季边界", "入口坐标"]),
    ("liushaoqi", "刘少奇在开封陈列馆", ["刘少奇陈列馆"], "scenic", "开封市北土街10号（百科候选）", "2A", ["src_search_a_level_ticket_map_20261023", "src_search_lesser_a_levels_20261023"], ["开放/预约/免费规则", "入口", "运营状态"]),
    ("yanqingguan", "延庆观", [], "scenic", "开封市鼓楼区观前街53号（第三方）", "2A", ["src_search_yanqingguan_20261023", "src_search_a_level_ticket_map_20261023"], ["目标日开放规则", "17:40/18:15 停入冲突", "入口"]),
    ("yuefeimiao", "岳飞庙", ["朱仙镇岳飞庙"], "scenic", "开封市祥符区朱仙镇岳庙大街（待核）", "2A", ["src_search_a_level_ticket_map_20261023", "src_search_lesser_a_levels_20261023"], ["目标日营业/票价", "地址/入口", "交通"]),
    ("zhangzhuang", "张庄", [], "scenic", None, "2A", ["src_search_a_level_ticket_map_20261023"], ["是否是可持续游客目的地", "地址/入口", "开放规则"]),
    ("kf_station", "开封站", ["开封火车站"], "transport", None, None, ["src_search_lesser_a_levels_20261023"], ["铁路出站口编号", "集合点", "2026 公交班次"]),
    ("kf_north_station", "开封北站", ["高铁开封北站"], "transport", None, None, ["src_search_lesser_a_levels_20261023"], ["铁路出站口编号", "公交/旅游专线当前班次", "集合点"]),
    ("gulou_night", "鼓楼夜市", ["开封鼓楼夜市"], "night_visit", "开封市鼓楼广场周边（政府页未给单点地址）", None, ["src_read_gulou_official_20261023", "src_search_night_market_20261023"], ["稳定开市/撤市时刻", "目标日临时休市", "具体摊位", "人流/排队实测"]),
    ("xisi_night", "西司夜市", [], "night_visit", "开封市包公湖西侧（待核）", None, ["src_search_night_market_20261023"], ["运营方页面", "目标日营业", "具体摊点"]),
    ("heda_night", "老河大夜市", ["河大夜市"], "night_visit", "河南大学老校区周边（待核）", None, ["src_search_night_market_20261023"], ["目标日营业", "具体摊点", "交通落点"]),
]
places = []
for pid, name, aliases, ptype, address, level, source_ids, missing in place_specs:
    places.append({"place_id": pid, "name": name, "aliases": aliases, "place_type": ptype,
                   "a_level": level, "address": address, "longitude": None, "latitude": None,
                   "coordinate_system": "unknown", "parent_place_id": None,
                   "evidence_status": "candidate_unverified", "source_ids": source_ids,
                   "missing": missing,
                   "note": "仅有名称/地址/规则候选；不把地图 POI 中心点当游客入口"})
# Core new restaurants are separate from A-level places.
restaurants = [
    ("huangjia_xisi", "黄家老店（西司店）", "开封市鼓楼区西司门街16号（平台候选）", ["food_guantangbao", "food_tongziji", "food_taohuasuni", "food_liyu_beimian"], ["src_search_huangjia_20261023"]),
    ("huangjia_daliang", "黄家老店（大梁路店）", "开封市城西街道大梁路18号（平台候选）", ["food_guantangbao"], ["src_search_huangjia_20261023"]),
    ("xingjia_daliangmen", "邢家锅贴老店·非遗·开封菜（大梁门总店）", "开封市西门大街98号（大梁门东50米路北，平台候选；另有地图页写198号）", ["food_guotie", "food_tongziji", "food_sanhen_tang"], ["src_search_xingjia_20261023"]),
    ("louzhonglou_xiaosong", "楼中楼酒店（汴梁小宋城店）", "大梁路汴梁小宋城小吃城一楼擂台东侧6号7号电梯（平台候选）", ["food_guantangbao", "food_chaoliangfen", "food_yangrou_kangmo"], ["src_search_louzhonglou_20261023"]),
    ("heyuan_hanxing", "宋都禾源·灌汤包子（汉兴路直营店）", "黄河大街东段与汉兴路交叉口东北角（平台候选）", ["food_guantangbao", "food_tongziji", "food_sanhen_tang"], ["src_search_heyuan_20261023"]),
    ("heyuan_jinxiang", "禾源灌汤包子（金祥路直营店）", "开封市金祥路金鼎立都北门西行20米路南（平台候选）", ["food_guantangbao", "food_liyu_beimian"], ["src_search_heyuan_20261023"]),
    ("diyilou_sihou", "第一楼（寺后街店）", "开封市寺后街8号（官网）", ["food_guantangbao", "food_liyu_beimian", "food_tongziji", "food_hongshuni"], ["src_diyilou_home_20261023", "src_diyilou_mingcai_20261023", "src_qunar_diyilou_20261023", "src_ctrip_diyilou_20261023"]),
]
for pid, name, address, _, source_ids in restaurants:
    places.append({"place_id": pid, "name": name, "aliases": [], "place_type": "restaurant", "a_level": None,
                   "address": address, "longitude": None, "latitude": None, "coordinate_system": "unknown",
                   "parent_place_id": None, "evidence_status": "candidate_unverified", "source_ids": source_ids,
                   "missing": ["目标日营业/最后接单", "当前门店菜单照片", "门店确认的供应关系", "准确入口/楼层指引", "实测用餐时长"],
                   "note": "平台地址/人均/推荐菜只作线索，不能拼成供应证明"})
dump("places-expanded.json", {"schema_version": "kaifeng-places-expanded/1", "generated_at": NOW,
                              "places": places,
                              "counts": {"a_level_scenics": sum(1 for p in places if p["a_level"]),
                                         "restaurants": sum(1 for p in places if p["place_type"] == "restaurant"),
                                         "transport": sum(1 for p in places if p["place_type"] == "transport"),
                                         "night_visits": sum(1 for p in places if p["place_type"] == "night_visit")}})

# Entrances only where a source explicitly names/relates one; no center-point invention.
entrances = [
    ("entry_qm_yingbin", "qingming", "qm_yingbin", "迎宾门", "entry", ["src_search_qingming_gates_20261023"], "official_public_account_and_third_party", "2023 攻略/节庆文章与 2026 第三方攻略；目标日通行待核"),
    ("entry_qm_duan", "qingming", "qm_duan", "端门", "entry", ["src_search_qingming_gates_20261023"], "official_public_account_and_third_party", "官方节选与第三方称可入园；存在第三方称仅节假日开放，冲突"),
    ("entry_qm_jinshui", "qingming", "qm_jinshui", "金水门", "entry", ["src_search_qingming_gates_20261023"], "official_public_account_and_third_party", "官方节选与第三方称可入园；目标日适用性待核"),
    ("entry_qm_danfeng", "qingming", "qm_danfeng", "丹凤门", "entry", ["src_search_qingming_gates_20261023"], "third_party_only", "第三方称常规关闭、活动期临时开放；不能当常规入口"),
    ("entry_hanyuan_south", "hanyuan", "hanyuan_south", "南大门", "both", ["src_search_hanyuan_20261023"], "third_party_only", "第三方介绍称南门靠近园林区；未取得当前通行/出口规则"),
    ("entry_hanyuan_north", "hanyuan", "hanyuan_north", "北大门", "both", ["src_search_hanyuan_20261023"], "third_party_only", "第三方介绍称北门靠近碑廊区；未取得当前通行/出口规则"),
    ("entry_wall_daliang", "wall", "wall_daliang", "大梁门古马道登城口", "entry", ["src_kf_wall_entrances_20260813", "src_kf_wall_closure_20260104"], "first_batch_candidate_reused", "2026-08 第三方仍列大梁门；2026-01 曾临时关闭，目标日通行未确认"),
    ("entry_wansuishan_east", "wansuishan", "wsd_east", "东大门", "entry", ["src_search_wansuishan_20261023"], "third_party_only", "第三方 2026-04 入口文章候选；运营方当前入口公告未取得"),
    ("entry_wansuishan_west", "wansuishan", "wsd_west", "西大门（原北门）", "entry", ["src_search_wansuishan_20261023"], "third_party_only", "第三方候选；旧/新门名关系待运营方确认"),
    ("entry_wansuishan_center", "wansuishan", "wsd_center", "中门", "entry", ["src_search_wansuishan_20261023"], "third_party_only", "第三方候选；无门点坐标/通行确认"),
    ("entry_daxiangguosi_main", "daxiangguosi", "dxgs_main", "大相国寺山门（待核）", "entry", ["src_search_daxiangguosi_20261023"], "not_explicit", "仅知道寺院地址，不能把山门/地图中心当已核验入口"),
    ("entry_tieta_main", "tieta", "tieta_main", "铁塔景区入口（待核）", "entry", ["src_search_tieta_20261023"], "not_explicit", "页面只写“景区入口处有讲解”，未给门名、坐标或方向"),
]
ent_rows = []
ent_places = []
for rid, parent, gate_id, gate_name, direction, source_ids, status, note in entrances:
    if status == "not_explicit":
        ent_places.append({"place_id": gate_id, "name": gate_name, "place_type": "entrance", "parent_place_id": parent,
                           "address": None, "longitude": None, "latitude": None, "coordinate_system": "unknown",
                           "evidence_status": "unresolved_placeholder", "source_ids": source_ids,
                           "missing": ["official gate name", "gate coordinate", "direction/passability"], "note": note})
        continue
    ent_places.append({"place_id": gate_id, "name": gate_name, "place_type": "entrance", "parent_place_id": parent,
                       "address": None, "longitude": None, "latitude": None, "coordinate_system": "unknown",
                       "evidence_status": status, "source_ids": source_ids,
                       "missing": ["gate coordinate", "target-date passability", "whether same gate allows exit"], "note": note})
    ent_rows.append({"entrance_relation_id": "rel_" + rid, "place_id": parent, "entrance_place_id": gate_id,
                     "direction": direction, "evidence_status": status, "source_ids": source_ids, "note": note,
                     "applicable_period": "未标明目标日期；必须按目标日复核"})
places.extend(ent_places)
# retain the augmented place aliases only in expanded places, not the base file
dump("places-expanded.json", {"schema_version": "kaifeng-places-expanded/1", "generated_at": NOW,
                              "places": places,
                              "counts": {"a_level_scenics": sum(1 for p in places if p.get("a_level")),
                                         "restaurants": sum(1 for p in places if p["place_type"] == "restaurant"),
                                         "transport": sum(1 for p in places if p["place_type"] == "transport"),
                                         "night_visits": sum(1 for p in places if p["place_type"] == "night_visit"),
                                         "named_entrances": sum(1 for p in places if p["place_type"] == "entrance")}})
dump("entrances-expanded.json", {"schema_version": "kaifeng-entrances-expanded/1", "generated_at": NOW,
                                 "entrances": ent_rows,
                                 "unresolved_placeholders": [e for e in ent_places if e["evidence_status"] == "unresolved_placeholder"],
                                 "notice": "未明确入口名的景区保留 placeholder，不用中心点补齐。"})

# Ticket rules: strings preserve exact source value; none is verified.
ticket_rows = [
    ("ticket_qm_2026_page", "qingming", "全价票 120 元；半价票 60 元（2026-09-17 第三方汇总）", "reservation_optional_candidate", None, ["src_read_tickets_20260917_20261023"], "third_party_current_page", "3日有效需按首次入园检票日；2026-10-01 至 10-05 的单日单次为另一来源的节庆规则，不能套目标日"),
    ("ticket_qm_public_account_festival", "qingming", "页面称 10/1-10/6 单日单次，10/7 起恢复 3 日无限次", "holiday_candidate_only", None, ["src_search_qingming_gates_20261023"], "official_public_account_excerpt", "官方公众号正文没有可确认的发布年份；不得把 10 月规则用于 2026-10-23 之后的目标日"),
    ("ticket_wansuishan", "wansuishan", "全价 100 元，含翰园碑林/开封城墙/铁塔公园权益，游玩日期起 3 日有效", "official_ticket_candidate", 100, ["src_search_wansuishan_ticket_20261023"], "official_page_excerpt", "目标日购票/预约、是否含其他园单次入园待确认"),
    ("ticket_kaifengfu_day", "kaifengfu", "日间成人 65 元、学生 32 元；17:30 停止日间入园", "operator_page_internal_conflict", 65, ["src_read_kaifengfu_guide_20261023"], "operator_page", "同页游客须知又写夏季 07:00-19:00/冬季 07:30-18:30；不合并"),
    ("ticket_kaifengfu_night", "kaifengfu", "夜游 90 元；17:00 开售，22:00 闭园", "operator_page_candidate", 90, ["src_read_kaifengfu_guide_20261023"], "operator_page", "目标日夜游是否开放、演出是否包含未确认"),
    ("ticket_tieta", "tieta", "全价 30 元；3 日有效（2026-09-17 第三方汇总）", "third_party_current_page", 30, ["src_read_tickets_20260917_20261023"], "third_party_page", "另一官网摘要给 2025-04-22 起成人 30 元；票效/目标日营业均待核"),
    ("ticket_daxiangguosi", "daxiangguosi", "全价 40 元，半价 20 元；购票后无需额外预约", "third_party_current_page", 40, ["src_read_tickets_20260917_20261023", "src_search_daxiangguosi_20261023"], "third_party_page", "未取得运营方当前页面；规则只作候选"),
    ("ticket_longting", "longting", "2026 门票页写 35/17 元；历史官网写 45/22 元", "conflicting_ticket_candidates", None, ["src_read_tickets_20260917_20261023", "src_search_longting_20261023"], "mixed", "2022 官网价格不是当前价；节会价与常规价不可混用"),
    ("ticket_hanyuan", "hanyuan", "全价 40 元（2025-12-18 第三方汇总）；另有 45/22 等旧线索", "third_party_candidate", 40, ["src_search_hanyuan_20261023"], "third_party_page", "只看官网免费入园页，不证明付费票种"),
    ("ticket_tianboyangfu", "tianboyangfu", "门市价 40 元；日常 8:00-18:30、夜游至 22:00（第三方）", "third_party_candidate", 40, ["src_search_tianboyangfu_20261023"], "third_party_page", "票效、夜游日期和停止入园冲突"),
    ("ticket_bao", "baogongci", "成人 30 元、半价 15 元（第三方 2026-01）", "third_party_candidate", 30, ["src_search_baogongci_20261023"], "third_party_page", "没有运营方票价原文"),
    ("ticket_xiaosongcheng", "xiaosongcheng", "街区免费；千回大宋等内部演出是否另票待核", "third_party_candidate", 0, ["src_search_xiaosong_20261023"], "third_party_page", "免费入场的商家/演出边界不清，不能当全场景免费"),
    ("ticket_wall", "wall", "全价 40 元、半价 20 元（第三方 2025/2026 页面）", "third_party_candidate", 40, ["src_search_a_level_ticket_map_20261023"], "third_party_page", "万岁山票含城墙权益的适用范围与本景区票种不能合并"),
]
tickets = [{"ticket_rule_id": r[0], "place_id": r[1], "price_text": r[2], "status": r[3], "adult_price_cny": r[4],
            "source_ids": r[5], "evidence_level": r[6], "note": r[7], "target_date": None} for r in ticket_rows]
dump("ticket-rules-expanded.json", {"schema_version": "kaifeng-tickets-expanded/1", "generated_at": NOW,
                                    "tickets": tickets,
                                    "notice": "价格/票效是候选，不代表目标日可购或适用；不采集身份证、订单和个人信息。"})
csv_write("ticket-rules-expanded.csv", [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()} for r in tickets],
          ["ticket_rule_id", "place_id", "price_text", "status", "adult_price_cny", "source_ids", "evidence_level", "note", "target_date"])

opening_rows = [
    ("open_qm_page", "qingming", "09:00-22:00；夜游 17:30 起；节目单有 22:05/22:30 条目", "page_time_candidate", ["src_search_qingming_gates_20261023"], ["节目晚于闭园", "页面未明确目标日适用", "停止入园未确认"]),
    ("open_wansuishan_official", "wansuishan", "08:00-22:00", "official_ticket_page_excerpt", ["src_search_wansuishan_ticket_20261023"], ["第三方 07:30/08:00-23:00、21:00/21:30 停检", "官网页没有目标日适用和停止入园"]),
    ("open_kaifengfu", "kaifengfu", "07:30-22:00 / 夏季 07:00-19:00 / 冬季 07:30-18:30", "operator_page_internal_conflict", ["src_read_kaifengfu_guide_20261023"], ["同页规则互斥", "17:30 停止日间入园仅适用于日票/日间"]),
    ("open_longting_official_old", "longting", "08:00-21:30（2022-08-04 官网游园指南）", "historical_official_candidate", ["src_search_longting_20261023"], ["2026 第三方：日常 08:00-18:30/夜游 22:00", "携程 08:00-19:00、18:00 停止入园"]),
    ("open_tieta", "tieta", "08:30-18:00（17:30 停入）/ 08:30-21:00（20:30 停入）", "conflicting_third_party_candidates", ["src_search_tieta_20261023"], ["2025-04 公告与 2026-03 平台页冲突", "官方目标日规则缺失"]),
    ("open_daxiangguosi", "daxiangguosi", "夏季 08:00-18:30 / 冬季 08:00-18:00；第三方另称停入提前 30 分钟", "third_party_candidate", ["src_search_daxiangguosi_20261023"], ["季节切换日未统一", "没有运营方当前公告"]),
    ("open_hanyuan", "hanyuan", "08:00-22:00（官网入口页）/ 21:00 停止入园（平台）", "official_and_third_party_conflict", ["src_search_hanyuan_20261023"], ["官网无停入时间", "平台时间未获运营方确认"]),
    ("open_tianboyangfu", "tianboyangfu", "日常 08:00-18:30 / 夜游 08:00-22:00；平台另称 20:30 停止入园", "third_party_conflict", ["src_search_tianboyangfu_20261023"], ["日常/夜游切换日不明", "无官网原文"]),
    ("open_xiaosongcheng", "xiaosongcheng", "10:00-23:00（平台）；23:00 停止入园 / 全天开放 / 23:30 等", "third_party_conflict", ["src_search_xiaosong_20261023", "src_read_xiaosong_ctrip_20261023"], ["运营方当前规则未取得", "内部演出票务另算"]),
    ("open_baogongci", "baogongci", "夏 07:00-18:30 / 冬 07:30-17:30；夏季夜游延至 22:00", "third_party_candidate", ["src_search_baogongci_20261023"], ["携程另称夏 07:00-22:00", "夜游仅在特定季节/条件"]),
    ("open_shanshangan", "shanshangan", "夏 08:00-18:00 / 冬 08:00-17:30；另一页夏 18:30/冬 17:00", "third_party_conflict", ["src_search_shanshangan_20261023"], ["两套第三方时间互斥", "运营方页面缺失"]),
    ("open_yanqingguan", "yanqingguan", "5/1-10/7 08:30-18:30（18:15 停入）；10/8-4/30 08:30-18:00（17:40 停入）", "third_party_candidate", ["src_search_yanqingguan_20261023"], ["另有春秋/夏季/冬季三套写法", "无运营方原文"]),
    ("open_wall", "wall", "08:30-22:00（21:30 停入）/ 08:00-22:00（21:00 停检）/ 日游 09:00-18:30、夜游 18:30-22:30", "conflicting_candidates", ["src_read_tourism_map_20261023", "src_kf_wall_hours_20260813"], ["2022 运营公告和 2026 第三方也不一致", "开放段/登城口可能不同"]),
    ("open_gulou_night", "gulou_night", "无官方稳定开市/撤市时刻；第三方 10:00-22:00 不可当官方营业时间", "official_management_without_hours", ["src_read_gulou_official_20261023", "src_search_night_market_20261023"], ["官方仅说统一入场/撤市", "2026-06-06 至 06-08 曾休市，已恢复"]),
]
opening = [{"rule_id": r[0], "place_id": r[1], "value_text": r[2], "evidence_status": r[3], "source_ids": r[4],
            "conflicts": r[5], "business_date": None, "opens_at": None, "closes_at": None, "last_entry_at": None,
            "note": "没有目标业务日期；字段不伪造时刻。需要景区/运营方按目标日确认。"} for r in opening_rows]
dump("opening-rule-candidates-expanded.json", {"schema_version": "kaifeng-opening-expanded/1", "generated_at": NOW,
                                               "rules": opening,
                                               "notice": "采集日不是游客目标日。不得把 2026-10-23 或节庆日期直接写入 OpenRule.business_date。"})

# programmes: retain the base catalogue and add the official Kaifengfu page's 24 listed items as undated.
base_programs = load(BASE / "structured" / "program-catalog.json")["programs"]
kf_text = raw_text("read_kaifengfu_guide_20261023.json")
kf_programs = []
for line in kf_text.splitlines():
    if not line.strip().startswith("*") or "演出地点" not in line and "时间：" not in line:
        continue
    clean = line.strip("* ").strip()
    if not clean or clean.startswith("节目单"):
        continue
    kf_programs.append({"program_name": clean.split(" 演出地点")[0].split(" 时间")[0].strip(), "stage_label": "见页面原文",
                        "listed_times": "页面仅给起始时间", "business_date": None, "ends_at": None,
                        "admission_deadline": None, "status": "undated_catalog_only", "source_id": "src_read_kaifengfu_guide_20261023"})
dump("program-catalog-expanded.json", {"schema_version": "kaifeng-program-expanded/1", "generated_at": NOW,
                                       "qingming": base_programs,
                                       "kaifengfu": kf_programs,
                                       "notice": "两个景区的节目都只有目录/起始时间，没有目标日期、结束时刻、取消状态，不能生成 ShowSession。"})

# Food catalogue and vendor-specific platform leads.
foods = [
    ("food_guantangbao", "小笼灌汤包子", ["灌汤包", "开封小笼包"], "皮薄馅多、汤汁丰富；具体配料与过敏原需门店确认", "通常含小麦面皮和猪肉馅，但这只是通用说明，不能用于个体忌口判断", ["src_search_food_kaifeng_snacks_20261023"]),
    ("food_liyu_beimian", "黄河鲤鱼焙面", ["鲤鱼焙面", "糖醋软熘鲤鱼带焙面"], "鲤鱼与细面搭配，酸甜/酥脆口感；不是每家店都供应", "含鱼、小麦、可能含蛋/乳/糖，需门店确认", ["src_search_food_kaifeng_snacks_20261023"]),
    ("food_tongziji", "桶子鸡", ["汴梁桶子鸡"], "开封传统熟食，常见为整鸡卤制、咸香耐嚼", "通常为禽肉；具体卤料和过敏原未知", ["src_search_food_kaifeng_snacks_20261023"]),
    ("food_siwei_cai", "四味菜", ["四味烩菜", "四味菜羊汤"], "常见组合为牛肉丁/丸子、面筋、黄花菜等，加羊汤；不同店家配方不完全相同", "含肉、麸质（面筋）等可能成分，必须问店家；不能断定清真/低敏", ["src_search_food_siwei_20261023"]),
    ("food_chaoliangfen", "炒凉粉", ["开封炒凉粉"], "红薯凉粉配豆豉/蒜/葱等炒制，外焦内嫩", "配料和辣度因摊点而异，过敏原未知", ["src_search_food_kaifeng_snacks_20261023"]),
    ("food_yangrou_kangmo", "羊肉炕馍", [], "薄面饼夹羊肉末，煎至焦香", "含小麦、羊肉；具体调料未知", ["src_search_food_kaifeng_snacks_20261023"]),
    ("food_guotie", "锅贴", ["开封锅贴"], "煎制面点，常见鲜肉韭黄/素馅，不等于每家都有同一馅料", "馅料需门店确认；通常含小麦", ["src_search_xingjia_20261023"]),
    ("food_taohuasuni", "炒红薯泥", ["红薯泥"], "红薯蒸熟后炒制，甜口，可能有蜂蜜/糖", "糖分较高；过敏原与配方需门店确认", ["src_search_huangjia_20261023"]),
    ("food_sanhen_tang", "三狠汤", [], "开封常见酸辣汤类菜，配方因店而异", "可能含蛋、醋、胡椒、肉类等，需门店确认", ["src_search_xingjia_20261023"]),
]
food_rows = [{"food_id": r[0], "name": r[1], "aliases": r[2], "brief": r[3], "allergen_note": r[4],
              "source_ids": r[5], "evidence_status": "cultural_description_candidate", "supply_proof": False} for r in foods]
dump("foods-expanded.json", {"schema_version": "kaifeng-foods-expanded/1", "generated_at": NOW, "foods": food_rows,
                             "notice": "菜品文化资料不等于任一门店当前供应；过敏原不能用通用配方猜。"})

vendors = []
vendor_specs = [
    ("vendor_huangjia_xisi", "huangjia_xisi", "黄家老店（西司店）", ["10:30-21:00（去哪儿/Trip）"], ["¥55 人均（平台）"], ["food_guantangbao", "food_tongziji", "food_taohuasuni", "food_liyu_beimian"], ["src_search_huangjia_20261023"], "third_party"),
    ("vendor_huangjia_daliang", "huangjia_daliang", "黄家老店（大梁路店）", ["10:30-22:00（Trip）"], ["未取得"], ["food_guantangbao"], ["src_search_huangjia_20261023"], "third_party"),
    ("vendor_xingjia_daliangmen", "xingjia_daliangmen", "邢家锅贴老店（大梁门总店）", ["11:00-22:30（平台）", "11:00-14:30/17:30-21:30（去哪儿攻略）"], ["¥53-57 人均（平台）"], ["food_guotie", "food_tongziji", "food_sanhen_tang"], ["src_search_xingjia_20261023"], "platform_conflict"),
    ("vendor_louzhonglou", "louzhonglou_xiaosong", "楼中楼酒店（汴梁小宋城店）", ["10:00-14:00/17:00-21:00（携程）"], ["¥61 人均（平台）"], ["food_guantangbao", "food_chaoliangfen", "food_yangrou_kangmo"], ["src_search_louzhonglou_20261023"], "third_party"),
    ("vendor_heyuan_hanxing", "heyuan_hanxing", "宋都禾源·灌汤包子（汉兴路直营店）", ["11:00-14:00/17:00-21:30（去哪儿攻略）"], ["¥54 人均（平台）"], ["food_guantangbao", "food_tongziji", "food_sanhen_tang"], ["src_search_heyuan_20261023"], "third_party"),
    ("vendor_heyuan_jinxiang", "heyuan_jinxiang", "禾源灌汤包子（金祥路直营店）", ["10:30 开始营业（携程）"], ["¥67 人均（平台）"], ["food_guantangbao", "food_liyu_beimian"], ["src_search_heyuan_20261023"], "third_party"),
    ("vendor_diyilou_sihou", "diyilou_sihou", "第一楼（寺后街店）", ["10:30-14:00/17:30-21:00（去哪儿）", "10:30 开始营业（携程）"], ["约 ¥55-66 人均（平台）"], ["food_guantangbao", "food_liyu_beimian", "food_tongziji", "food_hongshuni"], ["src_qunar_diyilou_20261023", "src_ctrip_diyilou_20261023"], "third_party"),
]
for vid, pid, name, hours, spend, food_ids, source_ids, status in vendor_specs:
    vendors.append({"vendor_id": vid, "place_id": pid, "name": name, "hours_candidates": hours,
                    "average_spend_candidates": spend, "last_order": None, "reservation": "unknown",
                    "meal_duration": None, "queue_time": None, "food_menu_leads": food_ids,
                    "evidence_status": status, "source_ids": source_ids,
                    "missing": ["目标日营业/最后接单", "商家菜单或现场确认", "真实入口/楼层", "排队与用餐实测"],
                    "menu_proof": "not_obtained", "supply_status": "candidate_not_supply_proof"})
dump("vendors-food-candidates-expanded.json", {"schema_version": "kaifeng-vendors-expanded/1", "generated_at": NOW,
                                               "vendors": vendors,
                                               "notice": "每家店独立记录；平台推荐菜/人均/营业时间仅作线索，不是当前菜单或供应证明。"})

supplies = []
for v in vendors:
    for fid in v["food_menu_leads"]:
        supplies.append({"supply_id": f"{v['vendor_id']}_{fid}", "vendor_id": v["vendor_id"], "food_id": fid,
                         "proof_type": "platform_menu_lead_only", "status": "candidate_not_supply_proof",
                         "source_ids": v["source_ids"], "evidence_note": "平台推荐菜/特色菜条目只说明页面列出，不等于目标日门店供应",
                         "required_proof": ["带日期和门店名的菜单照片", "商家公开菜单链接且明确门店", "现场点单/票据记录"]})
dump("supply-candidates-expanded.json", {"schema_version": "kaifeng-supply-expanded/1", "generated_at": NOW,
                                         "supplies": supplies, "notice": "所有供应关系均未达到验收。"})

meal_rows = []
for v in vendors:
    meal_rows.append({"vendor_id": v["vendor_id"], "name": v["name"], "average_spend_candidates": "; ".join(v["average_spend_candidates"]),
                      "meal_duration": "", "queue_time": "", "last_order": "", "evidence": "platform candidate only",
                      "formal_acceptance": "false"})
csv_write("meal-costs-expanded.csv", meal_rows, ["vendor_id", "name", "average_spend_candidates", "meal_duration", "queue_time", "last_order", "evidence", "formal_acceptance"])

# Transport: current officials and schedule candidates; no geocoded pickup point.
transport = [
    ("tr_small_loop_1", "小环线 1 号线", "08:00-20:00；平均发车间隔 10-15 分钟", "third_party_current_service_context", ["src_search_wansuishan_20261023"], ["来源为万岁山交通攻略的 2026-09 服务语境", "未取得运营方线路/站点/目标日公告", "站点与出口步行未核验"]),
    ("tr_small_loop_2", "小环线 2 号夜线", "20:00-23:30；平均发车间隔 10-15 分钟", "third_party_current_service_context", ["src_search_wansuishan_20261023"], ["来源为万岁山交通攻略的 2026-09 服务语境", "目标日夜间是否运营未知", "末班/站点未核验"]),
    ("tr_scenic_direct_lines", "景区直达线 1/3/5 号线", "线路名见 2026-09 交通攻略；无完整时刻", "third_party_line_name_only", ["src_search_wansuishan_20261023"], ["只有线路名，没有班次、票价、起终点", "不能生成集合点或路线"]),
    ("tr_bus_8_21_35_52_53", "8/21/35/52/53 路与开封北站相关", "2022 政府线网存在性；非 2026 时刻", "historical_official_route_candidate", ["src_kaifeng_bus_2022_20261023"], ["线路/站点当前状态待核", "不能给真实导航"]),
    ("tr_station_1", "开封站至清明上河园普通公交候选", "1 路 2022 政府站序存在；首末班 05:55-20:30；不等于旅游专线", "historical_official_route_candidate", ["src_kaifeng_bus_2022_20261023"], ["2026 班次/站点状态待核", "景区下车站到入口步行未核验", "不能把普通 1 路写成旅游公交 1 号线"]),
    ("tr_station_35", "开封北站至开封站方向 35 路候选", "2022 政府站序存在；首末班 06:00-19:00", "historical_official_route_candidate", ["src_kaifeng_bus_2022_20261023"], ["2026 班次/站点状态待核", "不等于北站直达景区专线"]),
    ("tr_station_52", "宋城路站至开封北站 52 路候选", "2022 政府站序存在；首末班 07:00-18:30", "historical_official_route_candidate", ["src_kaifeng_bus_2022_20261023"], ["2026 班次/站点状态待核", "不能证明与景区入口衔接"]),
]
tr_rows = []
for rid, name, value, status, source_ids, caveats in transport:
    tr_rows.append({"route_id": rid, "name": name, "schedule_candidate": value, "status": status,
                    "from_place_id": None, "to_place_id": None, "duration_minutes": None,
                    "source_ids": source_ids, "caveats": caveats + ["必须按目标时间向高德查询", "本文件不含可执行路线"]})
dump("transport-expanded.json", {"schema_version": "kaifeng-transport-expanded/1", "generated_at": NOW,
                                 "routes": tr_rows,
                                 "station_exit_status": {"kf_station": "no reliable current railway exit number",
                                                         "kf_north_station": "no reliable current railway exit number",
                                                         "kf_north_bus_stop": "not verified to be at station exit"},
                                 "notice": "2025 清明/2022 线网资料不是 2026 目标日实时班次；不提供虚构集合点或步行路线。"})

# Conflict register, source-backed and actionable.
conflicts = [
    {"conflict_id": "c_qm_close_program", "place_id": "qingming", "issue": "页面写 22:00 闭园，节目表却有 22:05/22:30 条目", "sources": ["src_search_qingming_gates_20261023"], "resolution": "未解决；不得用晚于闭园的节目时刻约束行程"},
    {"conflict_id": "c_kaifengfu_hours", "place_id": "kaifengfu", "issue": "同一官网页面写 07:30-22:00，游客须知又写夏 07:00-19:00/冬 07:30-18:30", "sources": ["src_read_kaifengfu_guide_20261023"], "resolution": "保留两套原文；目标日运营时间必须电话/公告确认"},
    {"conflict_id": "c_wansuishan_hours", "place_id": "wansuishan", "issue": "官网票务 08:00-22:00；第三方 07:30/08:00-23:00、21:00/21:30 停检", "sources": ["src_search_wansuishan_ticket_20261023", "src_search_wansuishan_20261023"], "resolution": "不合并；官网没有目标日和停检说明"},
    {"conflict_id": "c_tieta_hours", "place_id": "tieta", "issue": "08:30-18:00/17:30 停售 与 08:30-21:00/20:30 停入 同批出现", "sources": ["src_search_tieta_20261023"], "resolution": "需运营方当天公告/电话确认"},
    {"conflict_id": "c_hanyuan_close", "place_id": "hanyuan", "issue": "官网入口页 08:00-22:00；平台 21:00 停止入园", "sources": ["src_search_hanyuan_20261023"], "resolution": "待运营方确认闭园语义与目标日"},
    {"conflict_id": "c_xiaosong_hours", "place_id": "xiaosongcheng", "issue": "10:00-23:00、23:00 停止入园、全天开放、23:30 等说法并存", "sources": ["src_search_xiaosong_20261023"], "resolution": "不采用任意单一说法；运营方当前营业规则缺失"},
    {"conflict_id": "c_wall_hours", "place_id": "wall", "issue": "2022 运营公告、2026 第三方和首批记录给出不同闭园/停检时间", "sources": ["src_kfwt_wall_2022_20261023", "src_kf_wall_hours_20260813", "src_search_a_level_ticket_map_20261023"], "resolution": "按开放段/目标日向运营方确认"},
    {"conflict_id": "c_first_batch_date", "place_id": "qingming", "issue": "首批开放候选把采集日 2026-10-23 写进 business_date", "sources": ["src_first_batch_manifest_20261023"], "resolution": "扩展包不继承；business_date 全部为 null"},
    {"conflict_id": "c_xingjia_address", "place_id": "xingjia_daliangmen", "issue": "平台写西门大街 98 号，地图页写西门大街 198 号", "sources": ["src_search_xingjia_20261023"], "resolution": "地址候选二选一未定，不能生成导航点"},
    {"conflict_id": "c_station_exit", "place_id": "kf_north_station", "issue": "未取得可信铁路出站口编号；有文章含明显错误，已排除", "sources": ["src_search_lesser_a_levels_20261023"], "resolution": "保留未知，不猜 A/B/C 出口"},
]
dump("conflicts-expanded.json", {"schema_version": "kaifeng-conflicts-expanded/1", "generated_at": NOW,
                                 "conflicts": conflicts, "count": len(conflicts),
                                 "notice": "冲突不强行统一；候选页/目标日公告/现场核验才能关闭。"})

# Coverage of all 17 user categories, with explicit non-acceptance.
coverage = [
    (1, "首批服务范围", "expanded_scope_candidate: 24 A-level + stations + restaurants + markets", ["a-level-directory-expanded.json", "places-expanded.json"], "需要用户确定游玩日期、必看演出、预算/用餐偏好；24 个不是全部开封点位", False),
    (2, "地点基本信息", "names_and_some_addresses_partial", ["places-expanded.json"], "多数 A 级景区只有名录名称；地址、别名、边界和坐标未核验", False),
    (3, "实际出入口", "named_gate_candidates_plus_unresolved", ["entrances-expanded.json"], "无门点坐标、方向、通行限制和现场证据；车站出口未知", False),
    (4, "开放时间", "conflicting_candidates", ["opening-rule-candidates-expanded.json", "conflicts-expanded.json"], "目标日规则缺失；多个景区页面内部/不同来源冲突", False),
    (5, "门票与预约规则", "ticket_candidates_not_target_date", ["ticket-rules-expanded.json"], "目标日可购票种、实名/预约、联票权益和出园再入未闭环", False),
    (6, "园内必要道路", "not_obtained", ["conflicts-expanded.json"], "入口—目标舞台—出口的通行道路和耗时未取得", False),
    (7, "演出资料", "catalog_only_no_sessions", ["program-catalog-expanded.json", "opening-rule-candidates-expanded.json"], "缺目标日期、结束时间、提前到场、取消与舞台坐标", False),
    (8, "菜品介绍", "cultural_descriptions_partial", ["foods-expanded.json"], "完整配料和可靠过敏原说明未取得；不能推断适合忌口人群", False),
    (9, "真实餐馆", "branch_candidates_platform_data", ["vendors-food-candidates-expanded.json"], "当前营业、最后接单、预约、准确入口未由商家确认", False),
    (10, "餐馆与菜品对应", "platform_leads_only", ["supply-candidates-expanded.json"], "0 条供应证明；平台推荐菜不能作为当前菜单", False),
    (11, "用餐时间和费用", "platform_estimates_only", ["meal-costs-expanded.csv"], "用餐时长/排队未实测；人均为平台口径", False),
    (12, "城市转场", "schedule_candidates_no_live_route", ["transport-expanded.json"], "缺目标日公交/旅游专线适用性、车站出口和高德现时查询；不能生成可执行路线", False),
    (13, "休息与便民点", "not_expanded_this_round", ["places-expanded.json"], "仅有少量首批游客中心文字；厕所/饮水点/座位及开放限制未取得", False),
    (14, "图片与地图素材", "not_acquired_rights_unknown", ["source-manifest-expanded.json"], "未取得可商用图片/导览图授权；只保留链接和文本", False),
    (15, "每条资料的依据", "sources_with_hashes_limits", ["source-manifest-expanded.json", "conflicts-expanded.json"], "部分页面是搜索摘要/直读失败；核验人、有效日期和完整页码仍缺", False),
    (16, "更新负责人", "not_assigned", ["maintenance-expanded.json"], "没有真实负责人；不能声称已有更新责任链", False),
    (17, "真实走查记录", "not_performed", ["field-walkthrough-expanded.json"], "0 次完整现场走查；正式验收必需", False),
]
cov_rows = [{"no": n, "category": c, "status": s, "evidence_files": "; ".join(ev), "remaining": rem,
             "formal_acceptance": "false", "verified": "false"} for n, c, s, ev, rem, _ in coverage]
dump("coverage-17-expanded.json", {"schema_version": "kaifeng-coverage-expanded/1", "generated_at": NOW,
                                   "categories": cov_rows, "formal_acceptance_count": 0,
                                   "notice": "17 类全部为 false；不得将候选包当验收成果。"})
csv_write("coverage-17-expanded.csv", cov_rows, ["no", "category", "status", "evidence_files", "remaining", "formal_acceptance", "verified"])

dump("maintenance-expanded.json", {"schema_version": "kaifeng-maintenance-expanded/1", "generated_at": NOW,
                                   "assigned_owner": None, "assigned": False,
                                   "owner_required_fields": ["姓名/岗位", "负责范围", "复核证据位置", "有效日期"],
                                   "proposed_cadence": {"business_rules": "目标日前 24 小时与出发当天复核", "shows": "目标日开演前复核取消/加场/结束", "entrances_roads": "每次正式走查前复核", "menus_supply": "按门店当前菜单/票据复核", "source_rights": "每月复核一次"},
                                   "status": "unassigned_not_acceptance",
                                   "note": "本文件只是维护建议，不代表已有人负责。"})

dump("field-walkthrough-expanded.json", {"schema_version": "kaifeng-field-walk-expanded/1", "generated_at": NOW,
                                         "status": "not_performed", "walks": [], "walk_count": 0,
                                         "required_evidence": ["目标日期与实际到达/离开时间", "铁路/公交出站口到上客点", "入口换票/安检/步行耗时", "演出实际开始/结束/提前到场", "餐厅点单/等餐/用餐/离店", "临时休息/跳过一站/改线记录", "照片/票据/时间戳"],
                                         "formal_acceptance": False,
                                         "notice": "未开展现场走查；不得用平台距离替代实测。"})

dump("target-date-status-expanded.json", {"schema_version": "kaifeng-target-date-expanded/1", "generated_at": NOW,
                                          "target_business_date": None, "status": "blocked_missing_target_date",
                                          "reasons": ["用户未给出目标游玩日期", "景区节目/营业规则没有目标日可执行场次", "餐馆没有当前菜单/供应证明", "尚无完整现场走查"],
                                          "required_inputs": ["目标当地日期", "最早到达/最晚离开", "必看演出及可接受替代", "用餐时段/忌口/预算", "是否接受两段式用餐或备选餐厅"],
                                          "prohibited_outputs": ["把采集日当游玩日", "把搜索结果当运营方原文", "把品牌菜/平台推荐菜当供应证明", "把地图中心点当入口", "伪造公交班次/排队/场次结束"],
                                          "next_validation": ["景区按目标日确认入口/开闭园/停检/票效", "演出按目标日确认场次结束/取消/提前到场", "餐馆按目标日确认营业/最后接单/菜单供应", "现场走查及高德按需查询"]})

# Build a deliberately incomplete city-facts bundle. We include only identities and
# unchanged non-verified Source/Place/Food records; opening/show/vendor/supply data stays
# in the review tables because the strict schema requires dates/venue evidence we do not have.
cf_records = []
for s in sources:
    url = s["url"] if isinstance(s["url"], str) and s["url"].startswith(("http://", "https://")) else None
    cf_records.append({"fact_id": s["source_id"], "version": 1, "validation_status": "unverified", "business_status": "active",
                       "is_demo": False, "rights_status": "internal_only", "kind": "source", "name": s["name"],
                       "source_type": s["source_type"] if s["source_type"] in ("official", "operator", "field", "map_service", "other") else "other",
                       "url": url, "locator": s["locator"], "publisher": s["publisher"], "collected_at": s["captured_at"], "published_at": None})
place_ids = set()
for p in places:
    # Parent refs for entrance children are included; restaurant/source refs are still all internal.
    if p["place_id"] in place_ids:
        continue
    place_ids.add(p["place_id"])
    cf_records.append({"fact_id": p["place_id"], "version": 1, "source_ids": p["source_ids"],
                       "validation_status": "unverified", "business_status": "active", "is_demo": False,
                       "rights_status": "internal_only", "kind": "place", "name": p["name"],
                       "place_type": p["place_type"], "parent_place_id": p["parent_place_id"], "address": p["address"],
                       "longitude": None, "latitude": None, "coordinate_system": "unknown"})
for f in food_rows:
    cf_records.append({"fact_id": f["food_id"], "version": 1, "source_ids": f["source_ids"], "validation_status": "unverified",
                       "business_status": "active", "is_demo": False, "rights_status": "internal_only",
                       "kind": "food", "name": f["name"]})
dump("city-facts-candidate-expanded.json", {"schema_version": "city-facts/1", "records": cf_records})
(ROOT / "city-facts-candidate-expanded.json").write_text(
    (STRUCT / "city-facts-candidate-expanded.json").read_text()
)

# Coverage-ish scoring and a machine-readable summary.
summary = {"schema_version": "kaifeng-expanded-summary/1", "generated_at": NOW,
           "source_count": len(sources), "a_level_count": len(a_rows),
           "place_count": len({p["place_id"] for p in places}),
           "restaurant_count": sum(1 for p in places if p["place_type"] == "restaurant"),
           "named_entrance_relation_count": len(ent_rows),
           "program_count_qingming": len(base_programs), "program_count_kaifengfu": len(kf_programs),
           "food_count": len(food_rows), "vendor_count": len(vendors), "supply_candidate_count": len(supplies),
           "verified_count": 0, "formal_acceptance_count": 0, "executable_routes": 0,
           "target_business_date": None, "city_facts_records": len(cf_records)}
(REPORTS / "expanded-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
