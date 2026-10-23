#!/usr/bin/env python3
"""Emit supplementary CSV/JSON for facts city-facts/1 cannot represent."""
from __future__ import annotations
import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'structured'
REPORTS=ROOT/'reports'


def write_dry_run_summary():
    raw=json.loads((REPORTS/'city-facts-dry-run.json').read_text())
    kinds={}
    issues={}
    for record in raw['records']:
        kinds[record['kind']]=kinds.get(record['kind'],0)+1
        for issue in record['issues']:
            issues[issue]=issues.get(issue,0)+1
    summary={key:value for key,value in raw.items() if key!='records'}
    summary.update({
        'all_records_ready': all(record['ready'] for record in raw['records']),
        'ready_count': sum(record['ready'] for record in raw['records']),
        'record_kinds': dict(sorted(kinds.items())),
        'issue_counts': dict(sorted(issues.items(),key=lambda item:(-item[1],item[0]))),
        'note': '逐条校验结果保存在 city-facts-dry-run.json；此文件仅提供汇总，便于人工审阅。',
    })
    (REPORTS/'city-facts-dry-run-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')


def write_csv(name, rows, fields):
    with (S/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


tickets=[
 dict(ticket_id='ticket_qm_song_banquet_candidate', place_id='qingming', ticket_type='清明上河·宋宴', price_cny='悦席368/佳席468/尊席568；悦席/佳席儿童268', scope='含用餐当日及后2天共3天入园；园内补票不享受', reservation='实名制；官方小程序购买后可直接入园，其他平台需官方商城坐席预约', reentry='自用餐日起3日内无限次入园（该产品页口径）', applicable_period='页面未标日期；需目标日确认', source_ids=['src_qmsyun_song_banquet_20261023'], status='candidate_product_specific', limitations='不能外推到普通门票；未核验目标日库存/退改'),
 dict(ticket_id='ticket_qm_general_2023_holiday', place_id='qingming', ticket_type='普通景区门票（2023五一历史价）', price_cny='全价120；半价60', scope='2023五一攻略所述，不是当前票价', reservation='文中建议提前购票；非目标日预约规则', reentry='未确认', applicable_period='2023五一假期', source_ids=['src_qmsyun_gate_channels_20261023'], status='historical_only', limitations='过期活动价，不能用于当前报价'),
 dict(ticket_id='ticket_wall_2026_third_party', place_id='wall_daliang_scenic', ticket_type='城墙门票（含观影）', price_cny='全价40；半价20', scope='2026-08-13 第三方转述', reservation='线上公众号/美团/携程或窗口', reentry='未确认', applicable_period='2026-08文章，需景区确认', source_ids=['src_kf_wall_entrances_20260813','src_kf_wall_hours_20260813'], status='third_party_candidate', limitations='第三方；不能替代当前票务系统'),
]
write_csv('ticket-rules.csv',tickets,['ticket_id','place_id','ticket_type','price_cny','scope','reservation','reentry','applicable_period','source_ids','status','limitations'])

costs=[
 dict(cost_id='cost_diyilou_sihou', vendor_id='vendor_diyilou_sihou', per_person_cny=60, basis='携程用户反馈/页面显示约60-64元；不是门店报价', meal_duration_min=None, queue_min=None, status='estimate_not_measured', source_ids=['src_ctrip_diyilou_20261023'], note='不要承诺人均或排队时间；实际点单差异未知'),
]
write_csv('meal-costs.csv',costs,['cost_id','vendor_id','per_person_cny','basis','meal_duration_min','queue_min','status','source_ids','note'])

roads=[
 dict(road_id='road_qm_yingbin_to_song_banquet', from_id='qm_gate_yingbin', to_id='song_banquet_area', purpose='入口到宋宴/舞台候选动线', evidence_status='not_obtained', geometry=None, passable=None, source_ids=['src_qmsyun_song_banquet_20261023'], note='官方只说宋宴距金水门最近；没有入口到宴席的园内道路通行证据'),
 dict(road_id='road_qm_jinshui_to_song_banquet', from_id='qm_gate_jinshui', to_id='song_banquet_area', purpose='金水门到宋宴候选动线', evidence_status='relative_location_only', geometry=None, passable=None, source_ids=['src_qmsyun_song_banquet_20261023'], note='不能当作可导航路段；需官方导览图与现场核验'),
 dict(road_id='road_qm_stage_to_exit', from_id='qm_program_stage_candidates', to_id='qm_gate_candidates', purpose='演出后离园', evidence_status='not_obtained', geometry=None, passable=None, source_ids=[], note='缺目标舞台、散场分流和夜间出口规则'),
]
write_csv('internal-roads.csv',roads,['road_id','from_id','to_id','purpose','evidence_status','geometry','passable','source_ids','note'])

rights=[
 dict(asset_id='asset_qm_program_page', asset_type='web_text', owner_claim='清明上河园', rights_status='unknown', usage='internal_research_only', commercial_use=False, source_ids=['src_qmsyun_program_20261023'], note='不复制图片/长文案到对外物料'),
 dict(asset_id='asset_wall_weimeng_page', asset_type='web_text', owner_claim='开封市宋都城墙景区', rights_status='unknown', usage='internal_research_only', commercial_use=False, source_ids=['src_kfcqjq_weimeng_20261023'], note='网页公开可见不等于可商用'),
 dict(asset_id='asset_diyilou_photos', asset_type='photo', owner_claim='待授权', rights_status='not_obtained', usage='none', commercial_use=False, source_ids=[], note='尚无自拍或书面授权素材'),
]
write_csv('media-rights.csv',rights,['asset_id','asset_type','owner_claim','rights_status','usage','commercial_use','source_ids','note'])

write_csv('update-owners.csv',[dict(owner_id='owner_pending', scope='首批景区/餐馆/城墙资料', owner_name='', role='项目数据维护者（待指定）', cadence_days=7, event_day_check=True, assigned=False, evidence_location='', note='没有真实负责人，不得标记为已验证')],['owner_id','scope','owner_name','role','cadence_days','event_day_check','assigned','evidence_location','note'])

write_csv('field-walkthrough-required.csv',[],['walk_id','business_date','arrival_time','segment','planned_minutes','actual_minutes','exit_gate','entrance_gate','issue','adjustment','evidence_path','reviewer'])
write_dry_run_summary()
print('supplements written')
