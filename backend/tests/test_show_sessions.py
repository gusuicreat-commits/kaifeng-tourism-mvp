"""Team CSV and show queries use only synthetic catalogs and mock understanding."""
import csv
import importlib.util
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app import language
from app.db import connect
from app.city import facts
from app.city.agent import provider, understand
from app.city.agent.models import Action, Understanding
from app.city.agent import shows
from app.city.basket import ObjectRef, init_basket, resolve
from app.city.engine.context import PlanningContext
from app.city.engine.models import Goal, OperationalBundle
from app.city.engine.tools import plan_trip
from test_city_assistant import city, get
from test_city_agent import run, snapshot
from test_city_engine import sample, park_context

ROOT=Path(__file__).resolve().parents[2]
VENUES=ROOT/'data/mvp/show-venues-kaifengfu.json'
REFERENCE=ROOT/'docs/data_templates/show_program_reference.csv'
spec=importlib.util.spec_from_file_location('import_show_sessions',ROOT/'scripts/import_show_sessions.py')
importer=importlib.util.module_from_spec(spec);spec.loader.exec_module(importer)


def catalog(db):
    items=facts.records(db)
    program=items['program','program'].model_dump(mode='json')|{'version':2,'place_id':'park','aliases':['合成节目别名']}
    facts.import_bundle(facts.FactBundle(records=[program]),db,apply=True)
    return facts.records(db)


@pytest.fixture
def csv_catalog(tmp_path,sample):
    req,ctx=sample;ctx=park_context(ctx);database=tmp_path/'catalog.sqlite3'
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;facts.init_catalog(db)
        records=[r.model_copy(update={'is_demo':False}) for r in ctx.facts.values() if r.kind!='show_session']
        facts.import_bundle(facts.FactBundle(records=records),db,apply=True);items=catalog(db)
    return database,items


def row(items, **patch):
    values=[items['place','park'].name,items['program','program'].name,items['place','stage1'].name,
            '2030-05-01','11:00','11:30','10:55','合成资料采集','synthetic.png','测试采集人','2020-01-01']
    return dict(zip(importer.COLUMNS,values))|patch


def write_csv(tmp_path, rows):
    path=tmp_path/'shows.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=importer.COLUMNS);writer.writeheader();writer.writerows(rows)
    return path


def team_row(items, **patch):
    values={k:'' for k in importer.TEAM_COLUMNS}
    values.update(session_id='ses_test',program_id='prg_test',program_name='合成新节目（每周一）',program_status='待建节目档案',
                  park_place_id=items['place','park'].fact_id,park_name=items['place','park'].name,stage_name=items['place','stage1'].name,
                  session_date='2030-05-01',starts_at='11:00',ends_at='',duration_minutes='30',timezone='Asia/Shanghai',
                  session_status='scheduled',source='合成队员资料',checked_at='2020-01-01',evidence_path='evidence/team.png',
                  validation_status='candidate',reviewer='测试',version='1',notes='海报未标注日期，按采集日记录')
    values.update(patch);return values


def write_team_csv(tmp_path, rows):
    path=tmp_path/'team-shows.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=importer.TEAM_COLUMNS);writer.writeheader();writer.writerows(rows)
    return path


def counts(database):
    with sqlite3.connect(database) as db:
        return tuple(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('city_facts','city_fact_revisions'))


def test_import_normal_alias_source_reuse_and_repeat(csv_catalog,tmp_path):
    database,items=csv_catalog
    path=write_csv(tmp_path,[row(items,节目名称='合成节目别名'),row(items,开演='16:00',结束='16:30',停止入场='15:55')])
    assert importer.import_csv(path,database)=={'status':'imported','show_sessions':2,'sources':1}
    before=counts(database)
    assert importer.import_csv(path,database)['show_sessions']==0
    assert counts(database)==before
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;sessions=[s for s in facts.records(db).values() if s.kind=='show_session']
        assert all(s.planning_status=='reference' and s.validation_status=='unverified' and not s.reviewer for s in sessions)
        assert len({s.source_ids for s in sessions})==1


@pytest.mark.parametrize('patch,message',[
    ({'节目名称':'不存在的节目'},'节目名称'),({'舞台':'不存在的舞台'},'舞台名称'),
    ({'开演':'9:00'},'HH:MM'),({'开演':'25:00'},'HH:MM'),({'结束':'10:30'},'晚于'),
    ({'停止入场':'11:01'},'停止入场'),({'演出日期':'2030-02-30'},'YYYY-MM-DD'),
    ({'采集日期':'2099-01-01'},'未来'),({'采集人':''},'必填'),
])
def test_invalid_batch_writes_nothing(csv_catalog,tmp_path,patch,message):
    database,items=csv_catalog;before=counts(database)
    path=write_csv(tmp_path,[row(items,开演='16:00',结束='16:30'),row(items,**patch)])
    with pytest.raises(importer.ImportErrors) as error:importer.import_csv(path,database)
    assert any(e['row']==3 and message in e['error'] for e in error.value.errors)
    assert counts(database)==before


def test_duplicate_and_all_row_errors(csv_catalog,tmp_path):
    database,items=csv_catalog;before=counts(database)
    path=write_csv(tmp_path,[row(items),row(items),row(items,开演='bad',结束='bad'),row(items,节目名称='无')])
    with pytest.raises(importer.ImportErrors) as error:importer.import_csv(path,database)
    assert {e['row'] for e in error.value.errors}=={3,4,5}
    assert counts(database)==before


def test_dry_run_cli_and_example(csv_catalog,tmp_path,capsys):
    database,items=csv_catalog;before=counts(database)
    path=write_csv(tmp_path,[row(items),row(items,来源说明=importer.EXAMPLE)])
    assert importer.main([str(path),'--database',str(database),'--dry-run'])==0
    assert json.loads(capsys.readouterr().out)['show_sessions']==1
    assert counts(database)==before
    assert (ROOT/'docs/data_templates/show_sessions.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert importer.main([str(ROOT/'docs/data_templates/show_sessions.csv'),'--database',str(database),'--dry-run'])==0


def test_import_conflict_does_not_overwrite(csv_catalog,tmp_path):
    database,items=csv_catalog;path=write_csv(tmp_path,[row(items)])
    importer.import_csv(path,database);before=counts(database)
    write_csv(tmp_path,[row(items,结束='11:40')])
    with pytest.raises(importer.ImportErrors,match='整批拒绝'):importer.import_csv(path,database)
    assert counts(database)==before


def test_team_26_columns_builds_entities_skips_statuses_and_preserves_notes(csv_catalog,tmp_path):
    database,items=csv_catalog
    rows=[team_row(items),team_row(items,session_id='ses_test_2',program_name='合成新节目（周二）',stage_name='新舞台',ends_at='',duration_minutes=''),
          team_row(items,session_id='ses_stop',session_status='suspended_today',starts_at='',duration_minutes=''),
          team_row(items,session_id='ses_pending',session_status='to_be_announced',starts_at='',duration_minutes=''),
          team_row(items,session_id='ses_unknown',starts_at='',duration_minutes='')]
    path=write_team_csv(tmp_path,rows)
    report=importer.import_csv(path,database,dry_run=True)
    assert report['total_rows']==5 and report['importable_sessions']==2
    assert report['new_programs']==2 and report['new_stages']==1
    assert {x['reason'] for x in report['skipped_rows']}=={'今日停演','待定','无固定场次'}
    assert len(report['needs_team_info'])==2
    importer.import_csv(path,database)
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;records=facts.records(db)
    programs=[p for p in records.values() if isinstance(p,facts.Program) and p.name=='合成新节目']
    assert len(programs)==2 and {p.notes for p in programs}=={'原始节目名：合成新节目（每周一）','原始节目名：合成新节目（周二）'}
    sessions=[s for s in records.values() if isinstance(s,facts.ShowSession) and s.business_date.isoformat()=='2030-05-01']
    assert len(sessions)==2 and any(s.ends_at is None for s in sessions)
    source=[s for s in records.values() if isinstance(s,facts.Source) and '海报未标注日期' in s.name]
    assert source
    # The visitor says the name without its annotation; an unknown end still lists the start.
    ctx=PlanningContext(records,OperationalBundle(),datetime.fromisoformat('2030-05-01T08:00:00+08:00'))
    answer=shows.query(Action(kind='query',purpose='shows',target_name='合成新节目',quote='合成新节目今天几点演'),SimpleNamespace(planning=ctx))
    assert answer['status']=='answered' and len(answer['facts'])==2,answer
    assert '11:00–11:30' in answer['reply'] and '11:00–结束待确认' in answer['reply'] and '海报未标注日期' in answer['reply']
    unknown=next(s for s in sessions if s.ends_at is None)
    assert 'SHOW_END_UNKNOWN' in facts.gaps(unknown,records,unknown.starts_at)  # planning still treats it as not ready


def test_team_header_is_rejected_as_whole_batch(csv_catalog,tmp_path):
    database,_=csv_catalog;path=tmp_path/'bad-team.csv'
    path.write_text('session_id,program_name\nx,y\n',encoding='utf-8')
    with pytest.raises(importer.ImportErrors) as error:importer.import_csv(path,database,dry_run=True)
    assert '26 列' in error.value.errors[0]['error']


def test_imported_session_used_by_existing_program_planner(csv_catalog,tmp_path,sample):
    database,items=csv_catalog;path=write_csv(tmp_path,[row(items)])
    importer.import_csv(path,database)
    req,ctx=sample;ctx=park_context(ctx)
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;ctx=replace(ctx,facts=facts.records(db))
    req=req.model_copy(update={'goals':(Goal(goal_id='show',target_kind='program',target_id='program',kind='show',required=True),)})
    result=plan_trip(req,ctx)
    assert result.status=='feasible',result
    stop=result.itineraries[0].activities[0]
    assert stop.starts_at.hour==11 and ctx.facts['show_session',stop.session_id].planning_status=='reference'


def show_city(city,tmp_path):
    with connect() as db:
        from app.city.engine import service as engine
        ctx=park_context(engine.context(db))
        existing=facts.records(db)
        new=[r.model_copy(update={'version':existing[k].version+1}) if k in existing else r
             for k,r in ctx.facts.items() if k not in existing or r!=existing[k]]
        facts.import_bundle(facts.FactBundle(records=new),db,apply=True)
        items=catalog(db)
        db.execute("DELETE FROM city_facts WHERE kind='show_session'")
    path=write_csv(tmp_path,[row(items),row(items,开演='16:00',结束='16:30',停止入场='15:55')])
    with connect() as db:
        bundle,_=importer.build_bundle(path,db);facts.import_bundle(bundle,db,apply=True)
    return items


@pytest.mark.parametrize('wording',[
    '{name}下一场演出在哪里、几点？','今天{name}还可以看哪些演出？','帮我看看{name}的节目什么时候开演。',
])
@pytest.mark.parametrize('state',['available','missing','finished'])
def test_queries_variants_and_data_states(city,tmp_path,monkeypatch,wording,state):
    items=show_city(city,tmp_path);name=items['place','park'].name;text=wording.format(name=name)
    if state=='missing':
        with connect() as db:db.execute("DELETE FROM city_facts WHERE kind='show_session'")
    if state=='finished':city['clock'][0]=datetime.fromisoformat('2030-05-01T17:00:00+08:00')
    before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text);answer=task['result']
    if state=='available':
        assert task['status']=='answered',task
        assert '11:00–11:30' in answer['reply'] and '16:00–16:30' in answer['reply']
        assert items['place','stage1'].name in answer['reply'] and len(answer['facts'])==2
    elif state=='missing':assert task['status']=='needs_data' and '这几天的场次还没有资料' in answer['reply']
    else:assert task['status']=='answered' and '没有更多' in answer['reply'] and not answer['facts']
    assert get(city)==before


@pytest.mark.parametrize('clock',['下午三点以后','15:00之后','下午两点半往后'])
def test_query_explicit_clock_program_alias(city,tmp_path,monkeypatch,clock):
    show_city(city,tmp_path);name='合成节目别名';text=f'{name}{clock}还有什么场次？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='answered' and '16:00' in task['result']['reply'] and '11:00' not in task['result']['reply'],task


def test_invented_program_target_rejected(city,tmp_path,monkeypatch):
    show_city(city,tmp_path);text='今天还有什么演出？';name='合成节目别名'
    intent=Understanding(actions=(Action(kind='query',purpose='shows',target_name=name,quote=text),))
    understand.grounded(intent,text)  # an unmatched name is asked about, not a whole-turn failure
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(intent.model_dump(mode='json'),{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='needs_clarification' and not task['result'].get('facts')
    assert '没完全理解' not in json.dumps(task,ensure_ascii=False)


@pytest.mark.parametrize('target',['program','park',None])
def test_current_clock_and_generic_query_never_list_past_sessions(city,tmp_path,monkeypatch,target):
    items=show_city(city,tmp_path);city['clock'][0]=datetime.fromisoformat('2030-05-01T14:00:00+08:00')
    name=items['program','program'].name if target=='program' else items['place','park'].name if target else None
    text=f'{name or ""}接下来有哪些演出？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='answered' and len(task['result']['facts'])==1,task
    assert '16:00' in task['result']['reply'] and '11:00' not in task['result']['reply']


def test_missing_sessions_never_promotes_program_schedule(city,tmp_path,monkeypatch):
    items=show_city(city,tmp_path)
    with connect() as db:
        db.execute("DELETE FROM city_facts WHERE kind='show_session'")
        program=items['program','program'].model_dump(mode='json')|{'version':3,'schedule_text':'11:00、16:00','notes':'公告发布日期不是演出日期'}
        facts.import_bundle(facts.FactBundle(records=[program]),db,apply=True)
    name='合成节目别名';text=f'今天{name}几点开演？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='needs_data' and '时间只供参考' in task['result']['reply']
    assert not task['result']['facts'] and '11:00' not in task['result']['reply']


def test_stage_from_other_park_rejected(csv_catalog,tmp_path):
    database,items=csv_catalog
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        stage=items['place','stage1'].model_dump(mode='json')|{'version':2,'parent_place_id':None}
        facts.import_bundle(facts.FactBundle(records=[stage]),db,apply=True)
    before=counts(database);path=write_csv(tmp_path,[row(items)])
    with pytest.raises(importer.ImportErrors) as error:importer.import_csv(path,database)
    assert any('舞台名称' in e['error'] for e in error.value.errors) and counts(database)==before


@pytest.fixture
def venue_catalog(tmp_path):
    database=tmp_path/'venues.sqlite3'
    # The archived catalog is evidence for names and links, never for dated sessions.
    raw=json.loads((ROOT/'data/mvp/inventory-2026-10-25.json').read_text())
    for r in raw['records']:r['version']=1
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;facts.init_catalog(db)
        facts.import_bundle(facts.FactBundle.model_validate(raw),db,apply=True)
    return database


def test_reference_matching_before_after_and_remaining_failures(venue_catalog,capsys):
    database=venue_catalog;before=counts(database)
    report=importer.match_reference(REFERENCE,database)
    assert (report['total_rows'],report['candidate_rows'],report['matched_rows'],report['unmatched_rows'])==(424,181,171,10)
    assert len(report['incomplete_rows'])==243
    assert {e['code'] for e in report['errors']}=={'not_performance_venue','place_not_found'}
    assert counts(database)==before
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        bundle=facts.FactBundle.model_validate_json(VENUES.read_text())
        assert facts.import_bundle(bundle,db)['changed']==5
        facts.import_bundle(bundle,db,apply=True)
        assert facts.import_bundle(bundle,db,apply=True)['changed']==0
    before=counts(database)
    assert importer.main([str(REFERENCE),'--database',str(database),'--match-reference'])==1
    report=json.loads(capsys.readouterr().out)
    assert (report['candidate_rows'],report['matched_rows'],report['unmatched_rows'])==(181,179,2)
    assert [(e['row'],e['节目名称'],e['code']) for e in report['errors']]==[
        (165,'城北枪声','place_not_found'),(180,'王婆说媒','place_not_found')]
    assert all(e['row'] and e['error'] for e in report['incomplete_rows'])
    assert counts(database)==before


@pytest.mark.parametrize('index',range(5))
def test_sourced_venues_import_query_and_keep_original_poi(venue_catalog,tmp_path,index):
    database=venue_catalog;venues=facts.FactBundle.model_validate_json(VENUES.read_text())
    venue=venues.records[index]
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;init_basket(db)
        original=facts.records(db)['place',venue.parent_place_id]
        ref=ObjectRef(kind='poi',object_id=original.fact_id);basket=resolve(db,ref)
        facts.import_bundle(venues,db,apply=True)
        items=facts.records(db)
        assert items['place',original.fact_id]==original and original.place_type=='poi'
        assert resolve(db,ref)==basket
        assert facts.place_by_name(items,original.name)==original
    program=next(p for p in items.values() if isinstance(p,facts.Program)
                 and p.stage_name==original.name and p.place_id=='pl_wb_000003')
    path=write_csv(tmp_path,[dict(zip(importer.COLUMNS,[
        '开封府',program.name,original.name,'2030-05-01','11:00','11:30','',
        '合成场次，仅供测试','synthetic-venue.png','测试采集人','2020-01-01']))])
    assert importer.import_csv(path,database)['show_sessions']==1
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;items=facts.records(db)
    ctx=PlanningContext(items,OperationalBundle(),datetime.fromisoformat('2030-05-01T08:00:00+08:00'))
    assert ctx.parks(venue.fact_id)==ctx.parks(original.fact_id)==('pl_wb_000003',)
    text=f'今天{program.name}下一场演出几点？'
    answer=shows.query(Action(kind='query',purpose='shows',target_name=program.name,quote=text),SimpleNamespace(planning=ctx))
    assert answer['status']=='answered' and '11:00–11:30' in answer['reply'] and venue.name in answer['reply'],answer
    assert len(answer['facts'])==1
    assert items['show_session',answer['facts'][0]['id']].stage_place_id==venue.fact_id
    assert not ctx.ready(items['place',venue.fact_id],ctx.assessed_at)  # No surveyed coordinates were invented.
    # Visitors name the spot, not the program: the stage name finds that venue's sessions.
    text=f'今天{original.name}几点有节目？'
    answer=shows.query(Action(kind='query',purpose='shows',target_name=original.name,quote=text),SimpleNamespace(planning=ctx))
    assert answer['status']=='answered' and venue.name in answer['reply'],answer
    assert {items['show_session',f['id']].stage_place_id for f in answer['facts']}=={venue.fact_id}


def test_ordinary_poi_rejected_by_importer_and_fact_validator(csv_catalog,tmp_path):
    database,items=csv_catalog
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        poi=items['place','stage1'].model_copy(update={'version':2,'place_type':'poi'})
        facts.import_bundle(facts.FactBundle(records=[poi]),db,apply=True)
        path=write_csv(tmp_path,[row(items)])
        with pytest.raises(importer.ImportErrors) as error:importer.build_bundle(path,db)
        assert any(e.get('code')=='not_performance_venue' and e['舞台']==poi.name for e in error.value.errors)
        session=facts.ShowSession(kind='show_session',fact_id='invalid-poi-show',program_id='program',stage_place_id=poi.fact_id,
            business_date='2030-05-01',starts_at='2030-05-01T11:00:00+08:00',ends_at='2030-05-01T11:30:00+08:00')
        with pytest.raises(ValueError,match='独立舞台'):facts.import_bundle(facts.FactBundle(records=[session]),db,apply=True)


@pytest.mark.parametrize('mode',['alias','cancelled','ambiguous','other_parent'])
def test_parent_venue_matching_requires_unique_active_child(csv_catalog,tmp_path,mode):
    database,items=csv_catalog
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        poi=items['place','stage1'].model_copy(update={'version':2,'place_type':'poi','aliases':('合成院落别名',)})
        child=poi.model_copy(update={'version':1,'fact_id':'sourced-child','name':'合成演出点','aliases':(),
            'place_type':'stage','parent_place_id':poi.fact_id})
        if mode=='cancelled':child=child.model_copy(update={'business_status':'cancelled'})
        if mode=='other_parent':child=child.model_copy(update={'parent_place_id':'stage2'})
        additions=[poi,child]
        if mode=='ambiguous':additions.append(child.model_copy(update={'fact_id':'another-child'}))
        facts.import_bundle(facts.FactBundle(records=additions),db,apply=True)
    before=counts(database);path=write_csv(tmp_path,[row(items,舞台='合成院落别名')])
    if mode=='alias':
        assert importer.import_csv(path,database)['show_sessions']==1
        with sqlite3.connect(database) as db:
            db.row_factory=sqlite3.Row
            session=next(s for s in facts.records(db).values() if isinstance(s,facts.ShowSession))
            assert session.stage_place_id==child.fact_id
    else:
        with pytest.raises(importer.ImportErrors) as error:importer.import_csv(path,database)
        code='ambiguous' if mode=='ambiguous' else 'not_performance_venue'
        assert any(e.get('code')==code for e in error.value.errors) and counts(database)==before


def test_import_reports_each_missing_mismatched_or_ambiguous_venue(csv_catalog,tmp_path,capsys):
    database,items=csv_catalog
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        duplicate=items['place','stage1'].model_copy(update={'fact_id':'other-stage'})
        facts.import_bundle(facts.FactBundle(records=[duplicate]),db,apply=True)
    before=counts(database)
    path=write_csv(tmp_path,[row(items),row(items,舞台='不存在的场地'),row(items,舞台=items['place','stage2'].name),
                             row(items,景区='不存在的景区'),row(items,节目名称='不存在的节目')])
    assert importer.main([str(path),'--database',str(database)])==1
    errors=json.loads(capsys.readouterr().err)['errors']
    assert [(e['row'],e['code']) for e in errors]==[(2,'ambiguous'),(3,'place_not_found'),(4,'scenic_mismatch'),
                                                                 (5,'scenic_not_found'),(6,'program_not_found'),(6,'ambiguous')]
    assert all(e['error'] and e['field'] and e['舞台'] for e in errors)
    assert counts(database)==before


def test_exact_target_does_not_include_program_with_overlapping_alias(city,tmp_path,monkeypatch):
    items=show_city(city,tmp_path)
    with connect() as db:
        program=items['program','program'].model_dump(mode='json')|{'fact_id':'other','version':1,'name':'另一个节目','aliases':['合成节目']}
        session=next(s for s in facts.records(db).values() if s.kind=='show_session').model_dump(mode='json')|{'fact_id':'other-show','program_id':'other'}
        facts.import_bundle(facts.FactBundle(records=[program,session]),db,apply=True)
    text='合成节目别名今天还有哪些场次？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':'合成节目别名','quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='answered' and len(task['result']['facts'])==2,task
    assert all(s['program_id']=='program' for s in task['result']['facts'])


@pytest.mark.parametrize('wording,listed',[('后天{name}有什么演出？',('11:00','16:00')),('后天下午三点以后{name}还有演出吗？',('16:00',)),('下周六{name}有哪些演出？',())])
def test_a_named_later_day_lists_only_that_days_sessions(city,tmp_path,monkeypatch,wording,listed):
    items=show_city(city,tmp_path);city['clock'][0]=datetime.fromisoformat('2030-04-29T20:00:00+08:00')
    name=items['place','park'].name;text=wording.format(name=name)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text);reply=task['result'].get('reply','')
    if not listed:
        assert task['status']=='needs_data' and not task['result'].get('facts'),task;return
    assert task['status']=='answered' and '5月1日' in reply,task
    assert all(t in reply for t in listed) and len(task['result']['facts'])==len(listed)


@pytest.mark.parametrize('form',['part','park_prefixed','park_part'])
def test_shortened_or_park_prefixed_names_find_the_catalog_record(city,tmp_path,monkeypatch,form):
    items=show_city(city,tmp_path);city['clock'][0]=datetime.fromisoformat('2030-05-01T08:00:00+08:00')
    program=items['program','program'].name;park=items['place','park'].name
    name={'part':program[1:],'park_prefixed':park+program,'park_part':park[:-1]}[form]
    assert len(name)>=2;text=f'{name}今天几点有演出？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='answered' and len(task['result']['facts'])==2,task


def test_a_single_character_target_never_matches_by_containment(city,tmp_path,monkeypatch):
    items=show_city(city,tmp_path);name=items['program','program'].name[0];text=f'{name}有演出吗？'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert not task['result'].get('facts'),task


def test_a_failed_rule_parse_never_adds_its_placeholder_to_a_grounded_model_query(city,tmp_path,monkeypatch):
    items=show_city(city,tmp_path);city['clock'][0]=datetime.fromisoformat('2030-05-01T08:00:00+08:00')
    name=items['program','program'].name;text=f'{name}今天还演吗'
    # The rule extractor proposing an ungrounded change is what produced the placeholder.
    monkeypatch.setattr(understand,'rules',lambda *a,**k:Understanding(actions=(Action(kind='modify',quote='不在原话里'),)))
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','purpose':'shows','target_name':name,'quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['status']=='answered' and understand.ASK_QUESTION_OR_CHANGE not in json.dumps(task,ensure_ascii=False),task


def test_a_busy_day_lists_the_next_few_and_counts_the_rest(csv_catalog,tmp_path):
    database,items=csv_catalog
    rows=[team_row(items,session_id=f'ses_{i}',program_name='合成新节目',starts_at=f'{9+i//6:02d}:{i%6*10:02d}') for i in range(12)]
    importer.import_csv(write_team_csv(tmp_path,rows),database)
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row;records=facts.records(db)
    ctx=PlanningContext(records,OperationalBundle(),datetime.fromisoformat('2030-05-01T08:00:00+08:00'))
    answer=shows.query(Action(kind='query',purpose='shows',target_name='合成新节目',quote='合成新节目今天几点演'),SimpleNamespace(planning=ctx))
    assert len(answer['facts'])==shows.SHOWN and '09:00' in answer['reply'] and '之后还有 4 场' in answer['reply']
