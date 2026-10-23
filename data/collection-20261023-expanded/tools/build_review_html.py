#!/usr/bin/env python3
"""Render the expanded evidence pack as a self-contained review page."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRUCT = ROOT / "structured"
REPORTS = ROOT / "reports"


def load(name: str):
    return json.loads((STRUCT / name).read_text())


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


manifest = load("source-manifest-expanded.json")
directory = load("a-level-directory-expanded.json")
places = load("places-expanded.json")["places"]
entrances = load("entrances-expanded.json")
tickets = load("ticket-rules-expanded.json")["tickets"]
opening = load("opening-rule-candidates-expanded.json")["rules"]
programs = load("program-catalog-expanded.json")
foods = load("foods-expanded.json")["foods"]
vendors = load("vendors-food-candidates-expanded.json")["vendors"]
supplies = load("supply-candidates-expanded.json")["supplies"]
transport = load("transport-expanded.json")["routes"]
conflicts = load("conflicts-expanded.json")
coverage = load("coverage-17-expanded.json")
walkthrough = load("field-walkthrough-expanded.json")
maintenance = load("maintenance-expanded.json")
target = load("target-date-status-expanded.json")
dry = json.loads((REPORTS / "city-facts-dry-run.json").read_text())

sources = {s["source_id"]: s for s in manifest["sources"]}
place_by_id = {p["place_id"]: p for p in places}


def source_links(ids) -> str:
    links = []
    for source_id in ids:
        source = sources.get(source_id)
        if not source:
            continue
        label = source["publisher"] or source["name"]
        if source.get("url"):
            links.append(
                f'<a href="{esc(source["url"])}" target="_blank" rel="noopener">{esc(label)}</a>'
            )
        else:
            links.append(f'<span class="source-text">{esc(label)}</span>')
    return " · ".join(links) if links else '<span class="muted">来源待补</span>'


def place_name(place_id: str) -> str:
    return place_by_id.get(place_id, {}).get("name", place_id)


def status_badge(value: str) -> str:
    return f'<span class="tag">{esc(value)}</span>'


a_rows = "".join(
    f'<tr><td>{p["seq"]}</td><td><b>{esc(p["name"])}</b></td><td>{esc(p["level"])}</td></tr>'
    for p in directory["places"]
)

conflict_rows = "".join(
    f'<article class="conflict"><div><span class="conflict-no">{i:02d}</span><b>{esc(place_name(c["place_id"]))}</b></div>'
    f'<p>{esc(c["issue"])}</p><small>{source_links(c["sources"])}</small>'
    f'<div class="resolution">处理：{esc(c["resolution"])}</div></article>'
    for i, c in enumerate(conflicts["conflicts"], 1)
)

opening_rows = "".join(
    f'<tr><td><b>{esc(place_name(r["place_id"]))}</b><br><small>{esc(r["evidence_status"])}</small></td>'
    f'<td>{esc(r["value_text"])}</td><td>{esc("；".join(r["conflicts"]))}</td>'
    f'<td>{source_links(r["source_ids"])}</td></tr>'
    for r in opening
)

entrance_rows = "".join(
    f'<tr><td><b>{esc(next((p["name"] for p in places if p["place_id"] == e["entrance_place_id"]), e["entrance_place_id"]))}</b><br>'
    f'<small>{esc(place_name(e["place_id"]))}</small></td><td>{esc(e["direction"])}</td>'
    f'<td>{esc(e["evidence_status"])}</td><td>{esc(e["note"])}</td><td>{source_links(e["source_ids"])}</td></tr>'
    for e in entrances["entrances"]
)

transport_rows = "".join(
    f'<tr><td><b>{esc(r["name"])}</b><br><small>{esc(r["status"])}</small></td>'
    f'<td>{esc(r["schedule_candidate"])}</td><td>{esc("；".join(r["caveats"]))}</td>'
    f'<td>{source_links(r["source_ids"])}</td></tr>'
    for r in transport
)

vendor_rows = "".join(
    f'<tr><td><b>{esc(v["name"])}</b><br><small>{esc(v["place_id"])}</small></td>'
    f'<td>{esc(" / ".join(v["hours_candidates"]))}</td><td>{esc(" / ".join(v["average_spend_candidates"]))}</td>'
    f'<td>{esc("、".join(v["food_menu_leads"]))}</td><td>{esc(v["supply_status"])}</td>'
    f'<td>{source_links(v["source_ids"])}</td></tr>'
    for v in vendors
)

coverage_rows = "".join(
    f'<tr><td>{c["no"]}</td><td><b>{esc(c["category"])}</b></td><td>{status_badge(c["status"])}</td>'
    f'<td>{esc(c["remaining"])}</td></tr>'
    for c in coverage["categories"]
)

summary = json.loads((REPORTS / "expanded-summary.json").read_text())
ready_count = sum(1 for r in dry["records"] if r["ready"])

doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>开封扩展资料审阅 · 2026-10-23</title>
<style>
:root{{--ink:#17231d;--paper:#ece7da;--cream:#fbf8ef;--cinnabar:#a3382b;--ochre:#ad7c2f;--pine:#3e5d4b;--line:#d3c8b2;--muted:#6f685b;}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif SC","Songti SC",Georgia,serif;line-height:1.62}}
body:before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.18;background:repeating-linear-gradient(90deg,transparent 0 39px,#8b7e6520 40px),repeating-linear-gradient(0deg,transparent 0 39px,#8b7e6518 40px);z-index:-1}}
.shell{{max-width:1280px;margin:auto;padding:26px 28px 70px}}
nav{{display:flex;gap:18px;flex-wrap:wrap;padding:10px 0 18px;border-bottom:1px solid var(--line);font:12px/1.3 ui-monospace,SFMono-Regular,monospace;letter-spacing:.04em}}
nav a{{color:var(--pine);text-decoration:none;border-bottom:1px solid transparent}}nav a:hover{{border-color:var(--cinnabar)}}
header{{padding:44px 0 32px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,.7fr);gap:36px;align-items:end}}
.eyebrow{{font:11px/1.3 ui-monospace,monospace;letter-spacing:.2em;color:var(--cinnabar);text-transform:uppercase}}
h1{{font-size:clamp(42px,7vw,88px);line-height:.98;letter-spacing:-.055em;margin:14px 0 18px;font-weight:700}}
h1 span{{color:var(--cinnabar)}}
.lede{{font-size:16px;max-width:690px;color:#3e403a}}
.stamp{{border:1px solid var(--cinnabar);padding:18px 20px;background:#f4e6df;transform:rotate(-1deg);box-shadow:6px 7px 0 #ad7c2f22}}
.stamp b{{font-size:24px;display:block;color:var(--cinnabar)}}.stamp small{{color:#554e44}}
.metrics{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid var(--line);background:var(--cream);margin:20px 0 38px}}
.metric{{padding:16px 15px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}
.metric b{{display:block;font:32px/1 ui-monospace,monospace;color:var(--cinnabar)}}.metric span{{font-size:11px;color:var(--muted);letter-spacing:.04em}}
section{{margin:44px 0 0}}h2{{font-size:28px;margin:0 0 8px;letter-spacing:-.02em}}h2:before{{content:"§";color:var(--ochre);font:18px ui-monospace,monospace;margin-right:9px}}
.section-intro{{color:var(--muted);font-size:13px;max-width:850px;margin:0 0 16px}}
.notice{{border-left:5px solid var(--cinnabar);background:#fff7e9;padding:16px 18px;margin:18px 0;font-size:14px}}
.notice.ok{{border-color:var(--pine);background:#eaf1e9}}
.split{{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr);gap:22px}}
.panel{{background:var(--cream);border:1px solid var(--line);padding:19px}}
.panel h3{{margin:0 0 10px;font-size:18px}}ul.clean{{list-style:none;padding:0;margin:0}}ul.clean li{{padding:8px 0;border-bottom:1px dashed var(--line)}}ul.clean li:last-child{{border:0}}
.tags{{display:flex;flex-wrap:wrap;gap:6px}}.tag{{display:inline-block;font:10px/1.4 ui-monospace,monospace;background:#e9e1d0;color:#4a4439;padding:3px 7px;border-radius:2px;white-space:nowrap}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);background:var(--cream)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font:10px/1.4 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em;background:#e4dbc9;color:#585246}}tr:last-child td{{border-bottom:0}}
td b{{font-size:13px}}a{{color:#75431f;text-underline-offset:3px}}small,.muted{{color:var(--muted)}}.source-text{{color:var(--muted)}}
.conflict-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
.conflict{{background:var(--cream);border:1px solid var(--line);padding:14px 16px;min-width:0}}.conflict>div:first-child{{display:flex;gap:10px;align-items:baseline}}.conflict-no{{font:11px ui-monospace,monospace;color:var(--ochre)}}.conflict p{{margin:8px 0 7px;font-size:13px}}.resolution{{margin-top:9px;padding-top:8px;border-top:1px dashed var(--line);font-size:12px;color:var(--pine)}}
.a-grid{{columns:3;column-gap:26px;background:var(--cream);border:1px solid var(--line);padding:16px 20px}}
.a-grid div{{break-inside:avoid;padding:4px 0;border-bottom:1px dotted var(--line);font-size:13px}}.a-grid span{{display:inline-block;width:34px;color:var(--ochre);font:11px ui-monospace,monospace}}
.next{{display:grid;grid-template-columns:60px minmax(0,1fr);gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}}.next b{{font:24px/1 ui-monospace,monospace;color:var(--cinnabar)}}.next p{{margin:0}}
.footer{{margin-top:54px;padding-top:16px;border-top:1px solid var(--line);font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}
code{{font:11px ui-monospace,monospace;background:#e4dbc9;padding:2px 4px}}
@media(max-width:920px){{.shell{{padding:20px 18px 55px}}header{{grid-template-columns:1fr;padding-top:30px}}.metrics{{grid-template-columns:repeat(3,1fr)}}.metric{{border-bottom:1px solid var(--line)}}.split,.conflict-grid{{grid-template-columns:1fr}}.a-grid{{columns:2}}}}
@media(max-width:560px){{html,body{{max-width:100%;overflow-x:hidden}}.shell{{padding:14px 12px 44px;max-width:100vw}}nav{{gap:10px;font-size:10px}}h1{{font-size:42px}}header{{padding:24px 0 20px;gap:20px}}.stamp{{transform:none;box-shadow:none}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.metric b{{font-size:26px}}.a-grid{{columns:1}}table{{font-size:11px;min-width:660px}}th,td{{padding:8px}}.section-intro{{font-size:12px}}.footer{{display:block}}}}
</style>
</head>
<body>
<div class="shell">
<nav><a href="#scope">范围</a><a href="#conflicts">关键冲突</a><a href="#rules">开放与票务</a><a href="#gate">出入口</a><a href="#food">餐饮</a><a href="#transport">转场</a><a href="#coverage">17 类缺口</a><a href="#next">下一步</a></nav>
<header>
<div>
<div class="eyebrow">KAIFENG / EXPANDED EVIDENCE PACK / 2026.10.23</div>
<h1>扩大查一遍，<br><span>先把不知道留下。</span></h1>
<p class="lede">覆盖 24 个 A 级景区名称、重点景区规则、7 家餐馆分店、夜市与公交候选。页面只呈现可追溯候选与冲突，不把搜索摘要、平台推荐菜、地图中心点或历史节庆规则升级成目标日事实。</p>
</div>
<div class="stamp"><b>仍是候选包</b><small>目标游玩日未给出。没有一个景区入口、目标日演出场次、门店供应或现场走查达到正式验收。</small></div>
</header>
<div class="metrics">
<div class="metric"><b>{summary["source_count"]}</b><span>显式来源记录</span></div>
<div class="metric"><b>{summary["a_level_count"]}</b><span>A 级名录候选</span></div>
<div class="metric"><b>{summary["place_count"]}</b><span>地点/入口/餐馆候选</span></div>
<div class="metric"><b>{summary["program_count_qingming"] + summary["program_count_kaifengfu"]}</b><span>节目目录，未成为场次</span></div>
<div class="metric"><b>0</b><span>可执行路线</span></div>
</div>

<section id="scope">
<h2>这次扩大了什么</h2>
<p class="section-intro">A 级名单来自搜索正文的 24 行表格；官方页直读被 WAF 403 阻断，因此只能称为“名录搜索候选”。名单完整不等于每个景区都具备营业、入口与交通资料。</p>
<div class="split">
<div class="panel"><h3>A 级景区名单候选</h3><div class="a-grid">{"".join(f'<div><span>{p["level"]}</span>{esc(p["name"])}</div>' for p in directory["places"])}</div></div>
<div>
<div class="notice"><b>为什么仍不能排一天路线？</b><br>本次扩大的是“找得到谁”和“哪些规则互相打架”，不是把每个地点变成可访问目的地。多数 3A/2A 仍缺当前运营、精确地址、入口、公交落点和开放规则。</div>
<div class="panel"><h3>本批明确没有做</h3><ul class="clean"><li>没有把采集日 2026-10-23 当成游玩日</li><li>没有把平台推荐菜当成门店供应证明</li><li>没有用地图 POI 中心点补景区入口</li><li>没有把历史/节庆班次外推到目标日</li><li>没有开展现场走查或导航实测</li></ul></div>
</div>
</div>
</section>

<section id="conflicts">
<h2>关键冲突：先保留，不强行合并</h2>
<p class="section-intro">同一来源内部矛盾、运营方历史页面与当前第三方页面冲突、平台之间冲突，均分别保留原文。要关闭冲突，需要运营方按目标日确认或现场证据。</p>
<div class="conflict-grid">{conflict_rows}</div>
</section>

<section id="rules">
<h2>开放时间与票务候选</h2>
<p class="section-intro">下表保留来源原值。`business_date` 不在候选中伪造，因此没有“今天一定几点能进”的结论。</p>
<div class="table-wrap"><table><thead><tr><th>地点</th><th>页面原值/候选</th><th>冲突或边界</th><th>来源</th></tr></thead><tbody>{opening_rows}</tbody></table></div>
<div class="notice ok"><b>当前 dry-run：</b>接收 {len(dry["records"])} 条事实记录，ready {ready_count} 条，运行库仍为 0 行。软件能够安全拒绝不完整候选，不代表真实数据已可上线。</div>
</section>

<section id="gate">
<h2>实际出入口：只列被明确命名的门</h2>
<p class="section-intro">迎宾门、端门、金水门、大梁门等仅有文字候选；仍缺门点坐标、目标日通行、进/出规则与现场耗时。大相国寺、铁塔等没有明确门名时保留 placeholder，不用中心点冒充入口。</p>
<div class="table-wrap"><table><thead><tr><th>入口</th><th>方向</th><th>证据等级</th><th>边界</th><th>来源</th></tr></thead><tbody>{entrance_rows}</tbody></table></div>
</section>

<section id="food">
<h2>餐饮：分店先分开，供应另行证明</h2>
<p class="section-intro">黄家老店、邢家锅贴、楼中楼、宋都禾源、第一楼均按具体分店记录。营业时间、人均与推荐菜来自平台，不等于目标日营业、最后接单或菜单有售。</p>
<div class="table-wrap"><table><thead><tr><th>门店</th><th>平台营业候选</th><th>人均候选</th><th>菜品线索</th><th>供应状态</th><th>来源</th></tr></thead><tbody>{vendor_rows}</tbody></table></div>
<div class="notice"><b>{len(supplies)} 条菜品关联全部是 `candidate_not_supply_proof`。</b>当前 0 张带门店名和日期的菜单照片、0 条现场点单记录、0 次排队/用餐实测。</div>
</section>

<section id="transport">
<h2>城市转场：线路存在 ≠ 目标日可坐</h2>
<p class="section-intro">路线名、2022 政府站序和 2026 第三方服务语境分开记录。没有车站出站口编号、上客点、目标日班次和高德按需查询，不能生成集合点或可执行路线。</p>
<div class="table-wrap"><table><thead><tr><th>线路候选</th><th>时间/存在性候选</th><th>不能直接用的原因</th><th>来源</th></tr></thead><tbody>{transport_rows}</tbody></table></div>
</section>

<section id="coverage">
<h2>17 类资料覆盖</h2>
<p class="section-intro">全部 17 类仍是 `formal_acceptance=false`。这张表用于安排下一轮采集，不是完成度评分。</p>
<div class="table-wrap"><table><thead><tr><th>#</th><th>类别</th><th>本轮状态</th><th>还差什么</th></tr></thead><tbody>{coverage_rows}</tbody></table></div>
</section>

<section id="next">
<h2>下一轮最少需要确认什么</h2>
<p class="section-intro">先选有限样例，而不是继续铺全城。建议从“一个重点景区 + 一顿真实用餐 + 一个城市转场”开始闭环。</p>
<div class="next"><b>01</b><p><strong>给出目标日期和到达/离开时间。</strong>这是节目场次、营业规则和交通适用性的共同起点。</p></div>
<div class="next"><b>02</b><p><strong>选 1–2 场必看演出。</strong>由运营方确认目标日场次、结束、提前到场和取消状态。</p></div>
<div class="next"><b>03</b><p><strong>选 1 家具体分店。</strong>取得带日期和门店名的菜单/票据，确认最后接单和忌口信息。</p></div>
<div class="next"><b>04</b><p><strong>走一遍完整链路。</strong>车站出口 → 公交上客点 → 景区门 → 舞台 → 餐厅 → 下一站，记录实际耗时与临时改线。</p></div>
<div class="next"><b>05</b><p><strong>由维护人复核并留下有效日期。</strong>当前负责人未指定；维护建议不等于已有责任链。</p></div>
<div class="notice"><b>目标日状态：</b>{esc(target["status"])}。{esc("；".join(target["reasons"]))}。</div>
</section>

<div class="notice"><b>交接给其他对话：</b><br>同一项目直接发送目录 <code>/Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/collection-20261023-expanded</code>；跨项目或跨设备上传 <code>/Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/kaifeng-tourism-expanded-20261023.zip</code>。可直接复制的接收方提示见 <code>SHARE_PROMPT.md</code>，接收方先读 <code>HANDOFF.md</code>。</div>
<div class="footer"><span>完整证据：<code>data/collection-20261023-expanded</code></span><span>所有候选：unverified · internal_only · is_demo=false · formal_acceptance=false</span></div>
</div>
</body>
</html>
"""

(ROOT / "review.html").write_text(doc)
print(ROOT / "review.html")
