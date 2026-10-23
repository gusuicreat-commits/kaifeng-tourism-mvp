#!/usr/bin/env python3
from __future__ import annotations
import html, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'structured'
out=ROOT/'review.html'

def load(name):return json.loads((S/name).read_text())
manifest=load('source-manifest.json'); places=load('places.json')['places']; ent=load('entrance-candidates.json')['entrances']
openr=load('opening-rule-candidates.json')['rules']; prog=load('program-catalog.json')['programs']; foods=load('foods.json')['foods']
vendor=load('vendor-candidates.json')['vendors'][0]; supply=load('supply-candidates.json')['supplies']; transport=load('transport-candidates.json')['routes']
service=load('service-facilities.json')['facilities']; coverage=load('coverage-17.json')['categories']; target=load('target-date-status.json'); owners=load('responsibilities.json')
dry=load('../reports/city-facts-dry-run-summary.json')
src={s['source_id']:s for s in manifest['sources']}

def esc(x):return html.escape('' if x is None else str(x))
def srcs(ids):return ''.join(f'<a href="{esc(src[i]["url"])}" target="_blank" rel="noopener">{esc(src[i]["publisher"])}</a> ' for i in ids if i in src)
def chips(items):
    return ''.join(f'<span class="chip {esc(x["level"])}">{esc(x["text"])}</span>' for x in items)

status_zh={
'candidate_scope_prepared':'候选范围已定','candidate_names_addresses_partial':'名称/地址部分取得','official_names_third_party_access_candidates':'仅名称/转述入口候选','conflicting_unverified_candidates':'时间冲突，待核','partial_official_candidates':'部分官方候选','not_obtained':'未取得','catalog_no_target_date_sessions':'只有目录，无目标日场次','brand_level_candidates':'仅品牌级菜品','official_address_third_party_hours':'官方地址+第三方时间','brand_level_only_not_supply_proof':'仅品牌关联，非供应证明','partial_estimates':'估算/冲突','historical_route_candidates':'历史线路候选','official_text_no_coordinates':'官网文字，无坐标','link_only_rights_unknown':'仅链接，权限未知','manifest_with_hashes_and_limitations':'来源清单+哈希','proposed_not_assigned':'负责人未指定','not_performed':'未走查'}

rows=[]
for c in coverage:
    rows.append(f'<tr><td>{c["no"]}</td><td>{esc(c["category"])}</td><td><span class="badge {"ok" if c["formal_acceptance"] else "warn"}">{esc(status_zh.get(c["status"],c["status"]))}</span></td><td>{esc(c["remaining"])}</td></tr>')

place_rows=''.join(f'<tr><td><b>{esc(p["name"])}</b><br><small>{esc(p["place_id"])}</small></td><td>{esc(p["place_type"])}</td><td>{esc(p["address"] or "待核")}</td><td>{srcs(p["source_ids"])}</td><td>{esc("；".join(p["missing"]))}</td></tr>' for p in places)
ent_rows=''.join(f'<tr><td>{esc(next((p["name"] for p in places if p["place_id"]==e["entrance_place_id"]),e["entrance_place_id"]))}</td><td>{esc(e["direction"])}</td><td>{esc(e["applicable_period"])}</td><td>{esc(e["note"])}</td><td>{srcs(e["source_ids"])}</td></tr>' for e in ent)
open_rows=''.join(f'<tr><td>{esc(r["place_id"])}</td><td>{esc(r["business_date"])}</td><td>{esc(r.get("opens_at","—"))} – {esc(r.get("closes_at","—"))}</td><td>{esc(r.get("last_entry_at") or "未确认")}</td><td>{esc("；".join(r.get("conflicts",[])))}</td><td>{srcs(r["source_ids"])}</td></tr>' for r in openr)
prog_rows=''.join(f'<tr><td>{esc(p["program_name"])}</td><td>{esc(p["stage_label"])}</td><td>{esc(p["listed_times"])}</td><td>{esc("仅起始时刻" if p["parsed_start_times"] else "以景区公示为准")}</td></tr>' for p in prog)
food_rows=''.join(f'<tr><td>{esc(f["name"])}</td><td>{esc(f["brief"])}</td><td>{esc(f["dietary_note"])}</td><td>{srcs(f["source_ids"])}</td></tr>' for f in foods)
sup_rows=''.join(f'<tr><td>{esc(s["vendor_id"])}</td><td>{esc(s["food_id"])}</td><td>{esc(s["evidence_note"])}</td><td><span class="badge warn">{esc(s["status"])}</span></td></tr>' for s in supply)
tr_rows=''.join(f'<tr><td>{esc(x["from_place_id"])} → {esc(x["to_place_id"])}</td><td>{esc(x["line_candidate"])}</td><td>{esc(x.get("duration_minutes_candidate") or "未取得")}</td><td>{esc("；".join(x["caveats"]))}</td><td>{srcs(x["source_ids"])}</td></tr>' for x in transport)
svc_rows=''.join(f'<tr><td>{esc(x["facility_type"])}</td><td>{esc(x["location_text"])}</td><td>{esc(x["limitation"])}</td></tr>' for x in service)
conflicts=['清明上河园节目单写闭园 22:00，却列有 22:05/22:30 节目；不得用未解释的晚场时刻约束路线。','开封城墙：2022 运营公告 08:30–22:00/21:30 停检；2026-08 第三方入口页写大梁门 08:30–21:30；时间页写夏 22:00/冬 21:30；官网演出页只写演出 09:00–21:00/09:15–21:15。','第一楼营业：去哪儿 10:30–14:00/17:30–21:00；携程只确认 10:30 起；历史线索 10:00–21:00。','第一楼菜品：品牌官网有菜名，不等于寺后街店当日供应；携程推荐菜/用户点评只能作为线索。','车站：地图 POI/子 POI 不是乘客实际出站口或集合点；出站口编号需现场/官方确认。']
conf_html=''.join(f'<li>{esc(x)}</li>' for x in conflicts)

doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>开封首批资料候选审阅</title>
<style>
:root{{--ink:#18211d;--paper:#f4f0e5;--red:#9f2f24;--gold:#bc8b3d;--sage:#4f6958;--line:#d8cfbb;}}
*{{box-sizing:border-box}} body{{margin:0;overflow-x:hidden;background:linear-gradient(120deg,#e9e1cf,#f4f0e5 45%,#e5e0d4);color:var(--ink);font-family:Georgia,"Noto Serif SC","Songti SC",serif;line-height:1.65}}
.wrap{{width:100%;max-width:1240px;margin:auto;padding:34px 24px 70px;overflow-x:hidden}} header{{border-top:8px solid var(--red);padding:22px 0 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:20px;align-items:end}}
h1{{font-size:clamp(30px,5vw,58px);line-height:1.08;margin:0;letter-spacing:-.02em}} h2{{margin:36px 0 12px;font-size:25px;border-left:5px solid var(--gold);padding-left:12px}}
.kicker{{font:12px/1.4 ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;color:var(--red)}} .sub{{max-width:520px;font-size:14px;color:#514d43;overflow-wrap:anywhere}}
.alert{{margin:24px 0;padding:17px 20px;background:#fff8e8;border:1px solid #dfc88e;border-left:6px solid var(--red);font-size:15px}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}
.metric{{background:#fffdf7;border:1px solid var(--line);padding:13px}} .metric b{{display:block;font:30px/1.1 ui-monospace,monospace;color:var(--red)}} .metric span{{font-size:12px;color:#696256}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line)}} table{{width:100%;min-width:760px;border-collapse:collapse;background:#fffdf7;font-size:13px}} th,td{{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}} th{{font:11px/1.3 ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;background:#ebe4d4;position:sticky;top:0}}
.badge{{display:inline-block;padding:2px 7px;border-radius:99px;font:11px/1.4 ui-monospace,monospace;white-space:nowrap}} .badge.warn{{background:#f4dfdc;color:#72251e}} .badge.ok{{background:#dce9df;color:#26503a}}
.chip{{display:inline-block;padding:2px 7px;border-radius:3px;margin:0 2px 2px 0;font:11px ui-monospace,monospace;background:#ede7d8}} .chip.ok{{background:#dce9df}} .chip.warn{{background:#f4dfdc}}
a{{color:#6b3e20}} small{{color:#726b5e}} ul{{padding-left:22px}} li{{margin:6px 0}} .note{{font-size:12px;color:#655e51;background:#eee8da;padding:10px;border:1px dashed var(--line)}}
@media(max-width:760px){{html,body{{width:100%;max-width:100%;overflow-x:hidden}}.wrap{{padding:22px 14px 50px;max-width:100vw}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}header{{display:block}}.sub{{margin-top:10px;min-width:0}}th{{position:static}}table{{font-size:12px;min-width:680px}}.metric{{min-width:0;padding:10px}}.metric b{{font-size:24px}}}}
</style></head><body><div class="wrap"><header><div><div class="kicker">KAIFENG / EVIDENCE PROBE / 2026.10.23</div><h1>首批资料<br>候选审阅</h1></div><div class="sub">使用 <b>agent-reach</b> 的 Exa 网页搜索/读取后端采集。原始响应、结构化候选、来源哈希和严格缺口均保留在本地。页面只展示候选，不是正式业务事实。</div></header>
<div class="alert"><b>当前结论：</b>可以开始做第一批采编，但 <b>没有任何一条路线可执行或可称为已验证</b>。最关键的阻塞不是资料少，而是目标日期、真实入口/出口、目标日场次结束、城墙当前营业、门店实际供应和现场走查尚未闭环。<br><small>Schema dry-run：接收 {dry['received']} 条；ready {dry['ready_count']} 条；运行库仍为 0 行。</small></div>
<div class="grid"><div class="metric"><b>{len(manifest['sources'])}</b><span>来源记录</span></div><div class="metric"><b>{len(places)}</b><span>地点候选</span></div><div class="metric"><b>{len(prog)}</b><span>节目目录</span></div><div class="metric"><b>0</b><span>可执行/已核验路线</span></div></div>
<h2>17 类覆盖</h2><div class="note">“有候选”不等于“达到验收”。全部 17 类的 formal_acceptance 均为 false；表格里的缺口是本轮真正需要补的工作。</div><div class="table-wrap"><table><thead><tr><th>#</th><th>类别</th><th>本轮状态</th><th>还差什么</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>关键冲突</h2><ul>{conf_html}</ul>
<h2>地点候选</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>类型</th><th>地址候选</th><th>来源</th><th>缺口</th></tr></thead><tbody>{place_rows}</tbody></table></div>
<h2>入口候选</h2><div class="table-wrap"><table><thead><tr><th>入口</th><th>方向</th><th>适用期</th><th>说明</th><th>来源</th></tr></thead><tbody>{ent_rows}</tbody></table></div>
<h2>营业/开放规则候选</h2><div class="table-wrap"><table><thead><tr><th>地点</th><th>业务日</th><th>起—止</th><th>停止入园</th><th>冲突</th><th>来源</th></tr></thead><tbody>{open_rows}</tbody></table></div>
<h2>节目目录（仅 2026-10-23 页面标签；不是目标日场次）</h2><div class="table-wrap"><table><thead><tr><th>节目</th><th>页面舞台文字</th><th>页面列出时刻</th><th>可确认内容</th></tr></thead><tbody>{prog_rows}</tbody></table></div>
<h2>菜品与供应</h2><div class="table-wrap"><table><thead><tr><th>菜品</th><th>说明</th><th>忌口/过敏</th><th>来源</th></tr></thead><tbody>{food_rows}</tbody></table></div>
<div class="table-wrap"><table><thead><tr><th>商家</th><th>菜品</th><th>证据说明</th><th>状态</th></tr></thead><tbody>{sup_rows}</tbody></table></div>
<h2>城市转场候选</h2><div class="table-wrap"><table><thead><tr><th>起终点</th><th>线路</th><th>估时</th><th>不能直接用的原因</th><th>来源</th></tr></thead><tbody>{tr_rows}</tbody></table></div>
<h2>便民点与服务</h2><div class="table-wrap"><table><thead><tr><th>类型</th><th>文字位置</th><th>限制</th></tr></thead><tbody>{svc_rows}</tbody></table></div>
<h2>目标日状态</h2><div class="note"><b>状态：</b>{esc(target['status'])}。需要：{esc('、'.join(target['required_inputs']))}。禁止：{esc('、'.join(target['prohibited_outputs']))}。</div>
<h2>维护责任</h2><div class="note">{esc(owners['proposed_owner'])}；assigned={esc(owners['assigned'])}。建议景区/商家营业与票务每 7 天复核，演出在目标日 24 小时及当天开演前复核；但不代替真实负责人。</div>
<footer style="margin-top:38px;padding-top:14px;border-top:1px solid var(--line);font-size:12px;color:#6c6457">完整文件：<code>data/collection-20261023</code>；所有事实候选均为 internal_only 和 unverified；未写入运行库</footer>
</div></body></html>'''
out.write_text(doc)
print(out)
