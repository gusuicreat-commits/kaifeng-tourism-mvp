"""Trip-owned intentions, independent of formal stops. Display labels are never facts."""
import json
from datetime import datetime, time
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field
from ..db import connect
from ..memory.context import redact
from ..memory.storage import encoded, stamp
from . import facts, state
from .schema import Strict, EntityId
from .versions import read_draft
from .state_schema import CityDraftV2

Kind = Literal['scenic','food','place','poi','show','vendor','program']
FOODS = dict(zip(('xingrentea','huashenggao','chaoliangfen','xiaolongbao','taosibao','tongziji','yangroukangmo','liyubeimian','siweicai'),
                 ('宫廷杏仁茶','开封花生糕','炒凉粉','开封小笼包','套四宝','桶子鸡','羊肉炕馍','鲤鱼焙面','四味菜')))


class ObjectRef(Strict):
    kind: Kind
    object_id: EntityId


class BasketItem(ObjectRef):
    priority: Literal['must','want','optional'] = 'want'
    status: Literal['pending','standby'] = 'pending'
    note: str = Field(default='', max_length=300)
    source: Literal['home','detail','basket','assistant','legacy'] = 'detail'


class BasketMutation(Strict):
    request_id: UUID
    expected_version: int = Field(ge=0)
    operation: Literal['put','remove'] = 'put'
    item: BasketItem


def init_basket(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS city_baskets (
      trip_id TEXT PRIMARY KEY REFERENCES city_trip_states(id) ON DELETE CASCADE,
      version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS city_basket_items (
      trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      object_key TEXT NOT NULL, data_json TEXT NOT NULL, added_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, PRIMARY KEY(trip_id,object_key)
    );
    CREATE TABLE IF NOT EXISTS city_object_bindings (
      kind TEXT NOT NULL, object_id TEXT NOT NULL, fact_kind TEXT NOT NULL,
      fact_id TEXT NOT NULL, PRIMARY KEY(kind,object_id)
    );
    CREATE TABLE IF NOT EXISTS city_exploration_states (
      trip_id TEXT PRIMARY KEY REFERENCES city_trip_states(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, constraints_json TEXT NOT NULL,
      result_json TEXT, rounds INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
    );
    ''')


def version(db, trip_id):
    row=db.execute('SELECT version FROM city_baskets WHERE trip_id=?',(str(trip_id),)).fetchone()
    return row[0] if row else 0


def resolve(db, ref, items=None):
    """Local illustration IDs require explicit maintenance bindings, never name matching."""
    items=items if items is not None else facts.records(db)
    binding=db.execute('SELECT fact_kind,fact_id FROM city_object_bindings WHERE kind=? AND object_id=?',
                       (ref.kind,ref.object_id)).fetchone()
    fk={'scenic':'place','food':'food','place':'place','poi':'place','show':'show_session','vendor':'vendor','program':'program'}[ref.kind]
    identity=binding['fact_id'] if binding and binding['fact_kind']==fk else ref.object_id
    fact=items.get((fk,identity))
    # Old collector IDs remain resolvable after a canonical workbook merge.
    seen=set()
    while fact and getattr(fact,'superseded_by',None) and fact.fact_id not in seen:
        seen.add(fact.fact_id)
        identity=fact.superseded_by
        fact=items.get((fk,identity))
    # Local IDs can exist before the corresponding verified business record.
    if ref.kind=='scenic':
        row=db.execute('SELECT content FROM scenic WHERE id=?',(ref.object_id,)).fetchone()
        label=json.loads(row[0])['name'] if row else getattr(fact,'name',None)
    elif ref.kind=='food':label=FOODS.get(ref.object_id) or getattr(fact,'name',None)
    elif ref.kind=='vendor' and fact:label=getattr(items.get(('place',fact.place_id)),'name',None)
    else:label=getattr(fact,'name',None)
    if not label and ref.kind=='show' and fact:
        label=getattr(items.get(('program',fact.program_id)),'name',None)
    if not label:raise HTTPException(404,detail='对象资料待添加，不能加入未知对象。')
    if fact and fact.rights_status!='permitted' and not (
        fact.rights_status=='internal_only' and fact.planning_status=='reference'
    ):raise HTTPException(404,detail='对象资料尚未开放。')
    return {'key':ref.kind+':'+ref.object_id,'kind':ref.kind,'id':ref.object_id,'name':redact(label),
            'fact_kind':fk if fact else None,'fact_id':fact.fact_id if fact else None,
            'parent_place_id':getattr(fact,'parent_place_id',None),
            'data_status':'available' if fact else 'pending_addition'}


def snapshot(db, session, trip_id):
    row=state.owned(db,session,trip_id);items=facts.records(db)
    draft=read_draft(row['draft_json']);result=[]
    days=draft.days if isinstance(draft,CityDraftV2) else (draft,)
    for r in db.execute('SELECT * FROM city_basket_items WHERE trip_id=? ORDER BY added_at,object_key',(str(trip_id),)):
        saved=json.loads(r['data_json']);ref=BasketItem.model_validate(saved)
        try:value=resolve(db,ref,items)
        except HTTPException:value={'key':r['object_key'],'kind':ref.kind,'id':ref.object_id,'name':'资料待添加','fact_kind':None,'fact_id':None,'data_status':'pending_addition'}
        fid=value['fact_id'];stop_ids=[];arranged=[]
        for day,stop in ((day,s.model_dump(mode='json')) for day in days for s in day.stops):
            same=stop['session_id']==fid if value['fact_kind']=='show_session' else stop['place_id']==fid if value['fact_kind']=='place' else False
            if value['fact_kind']=='vendor' and fid:same=stop['place_id']==items['vendor',fid].place_id
            # A dish is arranged only when a planned dining stop has a verified supply relationship.
            if value['fact_kind']=='food' and fid and stop['kind']=='dining':
                moment=(day.starts_at or datetime.combine(day.business_date,time(),facts.TZ)) if isinstance(draft,CityDraftV2) else facts.now()
                same=any(isinstance(s,facts.Supply) and s.food_id==fid and facts.status(s,items,moment)['ready']
                    and items.get(('vendor',s.vendor_id)) and items['vendor',s.vendor_id].place_id==stop['place_id'] for s in items.values())
            if same and row['route_version']>0:
                stop_ids.append(stop['stop_id'])
                if isinstance(draft,CityDraftV2):arranged.append({'day_id':day.day_id,'stop_id':stop['stop_id']})
        value.update(priority=ref.priority,status='arranged' if stop_ids else ref.status,note=ref.note,source=ref.source,
                     added_at=r['added_at'],updated_at=r['updated_at'],stop_ids=stop_ids)
        if isinstance(draft,CityDraftV2):value['arranged_stops']=arranged
        result.append(value)
    return {'trip_id':str(trip_id),'version':version(db,trip_id),'items':result}


def read(session,trip_id):
    with connect() as db:return snapshot(db,session,trip_id)


def mutate(session,trip_id,body):
    data=body.model_dump(mode='json');scope='basket:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');row=state.owned(db,session,trip_id)
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        if row['status'] in state.TERMINAL:raise HTTPException(409,detail='这一程已结束，请切换或新建旅程。')
        if version(db,trip_id)!=body.expected_version:raise HTTPException(409,detail={'code':'BASKET_VERSION_CONFLICT','message':'素材篮已变化，请重新加载。'})
        value=resolve(db,body.item);key=value['key']
        old=db.execute('SELECT * FROM city_basket_items WHERE trip_id=? AND object_key=?',(str(trip_id),key)).fetchone()
        saved=body.item.model_dump(mode='json');saved['note']=redact(saved['note'])
        changed=bool(old) if body.operation=='remove' else not old or json.loads(old['data_json'])!=saved
        if changed:
            if body.operation=='remove':db.execute('DELETE FROM city_basket_items WHERE trip_id=? AND object_key=?',(str(trip_id),key))
            else:
                if not old and db.execute('SELECT count(*) FROM city_basket_items WHERE trip_id=?',(str(trip_id),)).fetchone()[0]>=40:raise HTTPException(422,detail='请先整理素材篮，最多保留40项。')
                db.execute('INSERT INTO city_basket_items VALUES (?,?,?,?,?) ON CONFLICT(trip_id,object_key) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at',
                           (str(trip_id),key,encoded(saved),stamp(),stamp()))
            db.execute('INSERT INTO city_baskets VALUES (?,1,?) ON CONFLICT(trip_id) DO UPDATE SET version=version+1,updated_at=excluded.updated_at',(str(trip_id),stamp()))
        result=snapshot(db,session,trip_id)
        state.remember(db,session,body.request_id,trip_id,scope,data,result)
        return result


def guard(db,trip_id,expected):
    if version(db,trip_id)!=expected:raise HTTPException(409,detail={'code':'BASKET_CHANGED','message':'素材篮已变化，旧预览失效，请重新计算。'})
