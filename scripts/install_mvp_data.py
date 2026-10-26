"""Adopt sourced team facts for MVP; fact writes use the maintained import CLI."""
import argparse
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from app.city.facts import FactBundle
from collector.open_rules_instantiator import run as instantiate_rules

OUT = ROOT / 'data/mvp'
TEAM = ROOT / 'data/team-handoff/city-facts-teamhandoff-20261023.json'
NOTE = '项目负责人授权将团队采集的有来源资料用于 MVP 参考；保留未人工核验状态，不保证实时营业、供应或入口通行。'


def build(day, collected_at=None):
    rows = copy.deepcopy(json.loads(TEAM.read_text())['records'])
    items = {(r['kind'], r['fact_id']):r for r in rows}
    workbook = next((ROOT / 'data/team-handoff').glob('*.xlsx'))
    rule_bundle, rule_report = instantiate_rules(workbook, day, team_bundle=TEAM)
    for r in rule_bundle['records']:
        items.setdefault((r['kind'], r['fact_id']), r)
    # Coordinates already captured in the team's declared coordinate system.
    coordinates = ROOT / 'data/collection-runs/coord_backfill_20261024/bundle/city-facts-candidate.json'
    if coordinates.exists():
        for r in json.loads(coordinates.read_text())['records']:
            key = r['kind'], r['fact_id']
            if key in items and r['kind']=='place' and r.get('longitude') is not None:
                items[key].update({k:r[k] for k in ('longitude','latitude','coordinate_system')})
    # Callers rebuilding an existing source bundle can retain its original
    # registration time rather than creating a no-op source revision.
    now = collected_at or datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    def source(fid, name, kind, locator, url=None):
        items['source',fid] = dict(kind='source',fact_id=fid,version=1,source_ids=[],name=name,
            source_type=kind,locator=locator,url=url,collected_at=now,validation_status='unverified',
            rights_status='internal_only',is_demo=False)
    source('src_mvp_amap','高德地点与道路预计耗时','map_service','AMap Web Service v5',
           'https://lbs.amap.com/api/webservice/guide/api/newroute')
    source('src_mvp_team_return','队员回填路线核验表','other','data/mvp/source/route-team-return.docx')
    # Values from this session's successful exact-name/branch AMap POI queries.
    points = {
        'pl_wb_000003':(114.346500,34.789726,'B017A007A3'),
        'pl_wb_vendor_000001':(114.351924,34.794059,'B017A00HWY'),
        'pl_wb_000013':(114.355996,34.793914,'B017A0N05A'),
    }
    for fid,(lon,lat,pid) in points.items():
        record=items['place',fid]
        record.update(longitude=lon,latitude=lat,coordinate_system='GCJ02')
        record['source_ids']=list(dict.fromkeys(record.get('source_ids',[])+['src_mvp_amap']))
    # Gate approach is a reference anchor, not a surveyed turn-by-turn entrance.
    gate = dict(kind='place',fact_id='pl_mvp_kaifengfu_gate',version=1,name='开封府府门广场入园衔接点',
        place_type='entrance',parent_place_id='pl_wb_000003',address='开封府府门广场，实际入口以现场标识为准',
        longitude=114.346783,latitude=34.789051,coordinate_system='GCJ02',
        source_ids=['src_mvp_amap','src_mvp_team_return'],validation_status='unverified',rights_status='internal_only')
    meeting = dict(gate, fact_id='pl_mvp_kaifengfu_meeting',name='开封府府门广场集合点',
                   place_type='transport',parent_place_id=None)
    items['place',gate['fact_id']]=gate;items['place',meeting['fact_id']]=meeting
    items['entrance','ent_mvp_kaifengfu'] = dict(kind='entrance',fact_id='ent_mvp_kaifengfu',version=1,
        source_ids=['src_mvp_team_return','src_mvp_amap'],place_id='pl_wb_000003',entrance_place_id=gate['fact_id'],
        direction='both',validation_status='unverified',rights_status='internal_only')
    if day == date(2026,10,25):
        # The returned form covers this date only. Never shift these answers to another day.
        for key,r in list(items.items()):
            if r['kind']=='open_rule' and r['place_id'] in points:
                del items[key]
        def rule(fid,pid,opens,closes,last=None,ticket=False,note=''):
            items['open_rule',fid] = dict(kind='open_rule',fact_id=fid,version=1,
                source_ids=['src_mvp_team_return'],validation_status='unverified',rights_status='internal_only',
                place_id=pid,business_date=day.isoformat(),opens_at=f'{day}T{opens}:00+08:00' if opens else None,
                closes_at=f'{day}T{closes}:00+08:00' if closes else None,
                last_entry_at=f'{day}T{last}:00+08:00' if last else None,
                reservation='not_required',reentry='unknown',ticket_required=ticket,
                ticket_scope=note or None)
        rule('open_mvp_kaifengfu_20261025','pl_wb_000003','17:30','22:00','21:00',True,'夜游需购票；不同票种的再入园规则不同，请按购票页面核对。')
        rule('open_mvp_firstfloor_20261025','pl_wb_vendor_000001','17:30','21:00','20:30',False)
        rule('open_mvp_gulou_20261025','pl_wb_000013','17:30',None,ticket=False,
             note='免费进入；回填收摊时刻含跨日歧义，闭市时间待确认，不把22点或23:59伪造成闭市时刻。')
        items['supply','vf_wb_000001']['source_ids']=['src_mvp_team_return']
        items['supply','vf_wb_000001']['evidence_note']='队员针对2026-10-25回填寺后街店有灌汤包；作为分店供应参考，现场售罄情况未知。'
        # The team observed this on 2026-10-25; that is when it was seen, not when it stops being sold.
        items['supply','vf_wb_000001'].update(observed_at='2026-10-25T00:00:00+08:00',effective_from=None,effective_to=None)
    for r in items.values():
        accepted = r['kind']!='supply' or (r['fact_id']=='vf_wb_000001' and day==date(2026,10,25))
        if accepted:
            r.update(planning_status='reference',planning_note=NOTE)
        # Manual verification fields stay as supplied; this adopts references, not verified facts.
    result=FactBundle.model_validate({'schema_version':'city-facts/1','records':list(items.values())})
    return result.model_dump(mode='json'), rule_report.to_dict(), points


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',type=date.fromisoformat,required=True)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    bundle,gaps,points=build(args.date)
    OUT.mkdir(parents=True,exist_ok=True)
    # Choose versions against the explicit target. The import CLI rechecks under its write lock.
    if args.database.exists():
        with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro',uri=True) as db:
            old={(r[0],r[1]):json.loads(r[2]) for r in db.execute('SELECT kind,id,record_json FROM city_facts')}
        for r in bundle['records']:
            before=old.get((r['kind'],r['fact_id']))
            if before:
                r['version']=before['version']
                if r!=before:r['version']+=1
    path=OUT/f'city-facts-reference-{args.date}.json'
    path.write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n')
    (OUT/f'open-rule-gaps-{args.date}.json').write_text(json.dumps(gaps,ensure_ascii=False,indent=2)+'\n')
    command=[sys.executable,str(ROOT/'scripts/import_city_facts.py'),str(path),'--database',str(args.database)]
    if args.apply:command.append('--apply')
    run=subprocess.run(command,capture_output=True,text=True,check=True,cwd=ROOT)
    report=json.loads(run.stdout)
    (OUT/f'import-{args.date}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if args.apply:
        from app.city.basket import init_basket
        with sqlite3.connect(args.database) as db:
            init_basket(db)
            bindings=[('scenic','kaifengfu','place','pl_wb_000003'),('scenic','qingming','place','pl_wb_000001'),
                ('scenic','wansui','place','pl_wb_000002'),('food','xiaolongbao','food','food_wb_000001')]
            bindings += [('amap_poi',pid,'place',fid) for fid,(_,_,pid) in points.items()]
            db.executemany('INSERT INTO city_object_bindings VALUES (?,?,?,?) ON CONFLICT(kind,object_id) DO UPDATE SET fact_kind=excluded.fact_kind,fact_id=excluded.fact_id',bindings)
    print(json.dumps({'status':report['status'],'received':report['received'],'ready':sum(r['ready'] for r in report['records']),
        'reference_bundle':str(path),'date':str(args.date)},ensure_ascii=False))


if __name__=='__main__':main()
