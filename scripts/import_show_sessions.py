#!/usr/bin/env python3
"""Import dated team-collected show sessions into an explicitly selected fact catalog."""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.city import facts
from app.city.schema import TZ
from app.memory.storage import now

COLUMNS=('景区','节目名称','舞台','演出日期','开演','结束','停止入场','来源说明','来源链接或截图文件名','采集人','采集日期')
REFERENCE_COLUMNS=('景区','节目名称（采集表请照抄）','舞台','公告时刻（仅供参考，不是某天的场次）')
EXAMPLE='示例，导入时跳过'


class ImportErrors(ValueError):
    def __init__(self, errors):
        self.errors=errors
        super().__init__('场次整批拒绝')


def identity(prefix, value):
    return prefix+hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:32]


def exact(item, name):return name in (item.name,*getattr(item,'aliases',()))


def within(items, place_id, scenic_id):
    seen=set()
    while place_id and place_id not in seen:
        if place_id==scenic_id:return True
        seen.add(place_id);place=items.get(('place',place_id))
        place_id=place.parent_place_id if place else None
    return False


def match_entities(row, items):
    issues=[]
    def reject(field, code, message):issues.append({'field':field,'code':code,'error':message})
    places=[p for p in items.values() if isinstance(p,facts.Place) and not p.superseded_by and p.business_status=='active']
    parks=[p for p in places if p.place_type=='scenic' and exact(p,row['景区'])]
    if len(parks)!=1:
        reject('景区','ambiguous' if parks else 'scenic_not_found','景区名称/别名对应多个景区' if parks else '找不到景区名称/别名对应的有效景区')
        return None,None,None,issues
    park=parks[0]
    programs=[p for p in items.values() if isinstance(p,facts.Program) and p.business_status=='active' and exact(p,row['节目名称'])]
    local=[p for p in programs if within(items,p.place_id,park.fact_id)]
    if len(local)!=1:
        code='ambiguous' if local else 'scenic_mismatch' if programs else 'program_not_found'
        reject('节目名称',code,{'ambiguous':'节目名称/别名在该景区对应多个节目','scenic_mismatch':'节目名称/别名存在，但所属景区不符',
                               'program_not_found':'找不到节目名称/别名对应的有效节目'}[code])
    program=local[0] if len(local)==1 else None
    named=[p for p in places if exact(p,row['舞台'])]
    local=[p for p in named if within(items,p.fact_id,park.fact_id)]
    stages=[p for p in local if p.place_type=='stage']
    if not stages:
        # A sourced child stage registers the POI's performance use without
        # renaming the visitor destination or making every POI a venue.
        parents={p.fact_id for p in local if p.place_type=='poi'}
        stages=[p for p in places if p.place_type=='stage' and p.parent_place_id in parents]
    if len(stages)!=1:
        code='ambiguous' if stages else 'not_performance_venue' if local else 'scenic_mismatch' if named else 'place_not_found'
        reject('舞台',code,{'ambiguous':'舞台名称/别名在该景区对应多个演出场所',
                            'not_performance_venue':'舞台名称/别名对应地点不是演出场所（类型：'+ '、'.join(sorted({p.place_type for p in local}))+ '）',
                            'scenic_mismatch':'舞台名称/别名对应地点存在，但所属景区不符',
                            'place_not_found':'找不到舞台名称/别名对应的有效地点'}[code])
    return park,program,stages[0] if len(stages)==1 else None,issues


def match_reference(csv_file, database):
    with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;items=facts.records(db)
    errors=[];incomplete=[];matched=0;total=0
    with Path(csv_file).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=list(REFERENCE_COLUMNS):raise ImportErrors([{'row':1,'error':'列名及顺序须与节目参考表一致'}])
        for line,raw in enumerate(reader,2):
            if not any(raw.values()):continue
            total+=1
            row={k:(raw.get(k) or '').strip() for k in REFERENCE_COLUMNS}
            row['节目名称']=row.pop(REFERENCE_COLUMNS[1])
            info={'row':line,**{k:row[k] for k in ('景区','节目名称','舞台')}}
            missing=[k for k in ('景区','节目名称','舞台') if not row[k]]
            if missing:
                incomplete.append({**info,'code':'missing_fields','error':'参考表缺少：'+'、'.join(missing)});continue
            if None in raw:
                errors.append({**info,'code':'invalid_columns','error':'列数不正确'});continue
            _,_,_,issues=match_entities(row,items)
            errors.extend({**info,**issue} for issue in issues)
            if not issues:matched+=1
    return {'status':'matched' if not errors and not incomplete else 'unmatched','total_rows':total,
            'candidate_rows':total-len(incomplete),'matched_rows':matched,
            'unmatched_rows':len({e['row'] for e in errors}),'errors':errors,'incomplete_rows':incomplete}


def build_bundle(csv_file, db):
    items=facts.records(db);errors=[];output={};seen=set();count=0
    with Path(csv_file).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=list(COLUMNS):raise ImportErrors([{'row':1,'error':'列名及顺序须与场次模板一致'}])
        for line,row in enumerate(reader,2):
            if not any(row.values()):continue
            if any('导入时跳过' in (v or '') for v in row.values()):continue  # the template's example row
            issues=['列数不正确'] if None in row else []
            row={k:(v or '').strip() for k,v in row.items() if k is not None}
            def reject(message):issues.append(message)
            if any(not row.get(k) for k in COLUMNS if k!='停止入场'):reject('除停止入场外各列必填')
            if len(row)!=len(COLUMNS):reject('列数不正确')
            days={};clocks={}
            for key in ('演出日期','采集日期'):
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',row.get(key,'')):raise ValueError()
                    days[key]=date.fromisoformat(row[key])
                except ValueError:reject(key+'须为有效 YYYY-MM-DD')
            if days.get('采集日期') and days['采集日期']>now().astimezone(TZ).date():reject('采集日期不能在未来')
            for key in ('开演','结束','停止入场'):
                if key=='停止入场' and not row.get(key):continue
                try:
                    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',row.get(key,'')):raise ValueError()
                    clocks[key]=time.fromisoformat(row[key])
                except ValueError:reject(key+'须为有效 HH:MM')
            if '结束' in clocks and '开演' in clocks and clocks['结束']<=clocks['开演']:reject('结束必须晚于开演，不支持跨日')
            if '停止入场' in clocks and '开演' in clocks and clocks['停止入场']>clocks['开演']:reject('停止入场不能晚于开演')
            _,program,stage,mismatches=match_entities(row,items)
            if issues or mismatches:
                info={'row':line,**{k:row[k] for k in ('景区','节目名称','舞台')}}
                errors.extend({**info,'error':issue} for issue in issues)
                errors.extend({**info,**issue} for issue in mismatches);continue
            day=days['演出日期']
            starts=datetime.combine(day,clocks['开演'],TZ);ends=datetime.combine(day,clocks['结束'],TZ)
            key=(program.fact_id,starts)
            if key in seen:
                errors.append({'row':line,'error':'同一节目同一开演时刻重复'});continue
            seen.add(key)
            metadata=dict(planning_status='reference',planning_note='队员采集的带日期场次参考资料，尚未人工核验',rights_status='internal_only')
            locator=row['来源链接或截图文件名']
            try:
                source=facts.Source(kind='source',fact_id=identity('show_source:',[row[k] for k in COLUMNS[7:]]),
                    name=row['来源说明'],source_type='other',publisher=row['采集人'],locator=locator,
                    url=locator if locator.startswith(('https://','http://')) else None,
                    collected_at=datetime.combine(days['采集日期'],time(),TZ),**metadata)
                session=facts.ShowSession(kind='show_session',fact_id=identity('show:',[program.fact_id,starts.isoformat()]),
                    program_id=program.fact_id,stage_place_id=stage.fact_id,business_date=day,starts_at=starts,ends_at=ends,
                    admission_deadline=datetime.combine(day,clocks['停止入场'],TZ) if '停止入场' in clocks else None,
                    source_ids=(source.fact_id,),effective_from=datetime.combine(day,time(),TZ),
                    effective_to=datetime.combine(day+timedelta(days=1),time(),TZ),**metadata)
                existing=[s for s in items.values() if isinstance(s,facts.ShowSession) and (s.program_id,s.starts_at)==key]
                if existing:
                    if len(existing)!=1:raise ValueError('库内同节目同一时刻有多条场次，请先整理')
                    before=existing[0]
                    if any(getattr(before,k)!=getattr(session,k) for k in ('stage_place_id','ends_at','admission_deadline')):
                        raise ValueError('已有同节目同一时刻场次内容冲突，请走事实升版流程')
                    continue
                output['source',source.fact_id]=source;output['show_session',session.fact_id]=session;count+=1
            except ValueError as exc:errors.append({'row':line,'error':str(exc)})
    if errors:raise ImportErrors(errors)
    bundle=facts.FactBundle(records=tuple(output.values()))
    facts.import_bundle(bundle,db)
    return bundle,count


def import_csv(csv_file, database, dry_run=False):
    # Validate a read-only snapshot first; dry-run never initializes or alters the target.
    with sqlite3.connect(':memory:') as memory:
        memory.row_factory=sqlite3.Row
        with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro',uri=True) as source:source.backup(memory)
        facts.init_catalog(memory)
        bundle,count=build_bundle(csv_file,memory)
    if not dry_run:
        with sqlite3.connect(database,timeout=15) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            bundle,count=build_bundle(csv_file,db)
            facts.import_bundle(bundle,db,apply=True)
    return {'status':'dry_run' if dry_run else 'imported','show_sessions':count,'sources':sum(r.kind=='source' for r in bundle.records)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file',type=Path)
    parser.add_argument('--database',type=Path,required=True,help='显式选择已有事实库')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--match-reference',action='store_true',help='只核对节目参考表的名称关联，不读取公告时刻、不创建场次')
    args=parser.parse_args(argv)
    try:
        if args.match_reference:
            report=match_reference(args.csv_file,args.database)
            print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='matched' else 1
        print(json.dumps(import_csv(args.csv_file,args.database,args.dry_run),ensure_ascii=False))
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'rejected','errors':exc.errors if isinstance(exc,ImportErrors) else [str(exc)]},ensure_ascii=False),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
