"""All private engine objects cascade with their existing city trip and M7 session."""
import json
from ...memory.storage import encoded, stamp
from .models import OperationalBundle, Journey, PlaceCost
from ..facts import Source


def init_engine(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS city_engine_operations (
      kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
      PRIMARY KEY(kind,id)
    );
    CREATE TABLE IF NOT EXISTS city_engine_operation_revisions (
      kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
      PRIMARY KEY(kind,id,revision)
    );
    CREATE TABLE IF NOT EXISTS city_engine_candidates (
      id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      base_version INTEGER NOT NULL, fingerprint TEXT NOT NULL, requirements_json TEXT NOT NULL,
      result_json TEXT NOT NULL, progress_json TEXT NOT NULL, expires_at TEXT NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS city_engine_routes (
      trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      route_version INTEGER NOT NULL, state_version INTEGER NOT NULL, requirements_json TEXT NOT NULL,
      itinerary_json TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(trip_id,route_version)
    );
    ''')


def load_operations(db):
    journeys=[];costs=[]
    for row in db.execute('SELECT * FROM city_engine_operations ORDER BY kind,id'):
        (journeys if row['kind']=='journey' else costs).append(
            (Journey if row['kind']=='journey' else PlaceCost).model_validate_json(row['content']))
    return OperationalBundle(journeys=tuple(journeys),costs=tuple(costs))


def import_operations(db,bundle,catalog,apply=False):
    incoming=[('journey',j.journey_id,j.revision,j) for j in bundle.journeys]+[
        ('cost',c.place_id,c.revision,c) for c in bundle.costs]
    seen=set();changed=[]
    for kind,identity,revision,record in incoming:
        if (kind,identity) in seen:raise ValueError('运营记录重复 ID')
        seen.add((kind,identity))
        ids=([record.travel.origin_place_id,record.travel.destination_place_id]+[b.gate_place_id for b in record.boundaries]
             +[b.park_id for b in record.boundaries]) if kind=='journey' else [record.place_id]
        if any(('place',pid) not in catalog for pid in ids):raise ValueError('运营记录引用不存在的地点')
        if kind=='journey' and any(not isinstance(catalog.get(('source',sid)),Source) for sid in record.source_ids):
            raise ValueError('交通记录引用不存在的来源')
        previous=db.execute('SELECT * FROM city_engine_operations WHERE kind=? AND id=?',(kind,identity)).fetchone()
        content=record.model_dump_json()
        if previous and json.loads(previous['content'])==json.loads(content):continue
        if revision!=(previous['revision']+1 if previous else 1):raise ValueError('运营记录版本冲突')
        changed.append((kind,identity,revision,content))
    if apply:
        for row in changed:
            db.execute('INSERT OR REPLACE INTO city_engine_operations VALUES (?,?,?,?)',row)
            db.execute('INSERT INTO city_engine_operation_revisions VALUES (?,?,?,?)',row)
    return {'status':'imported' if apply else 'dry_run','received':len(incoming),'changed':len(changed),
            'notice':'格式/引用校验不证明交通、费用或授权真实有效；规划时另验时效和通行条件。'}


def export_engine(db,trip_id):
    from ..versions import read_requirements, read_itinerary, read_result
    result={'versions':[dict(r) for r in db.execute('SELECT route_version,state_version,requirements_json,itinerary_json,created_at FROM city_engine_routes WHERE trip_id=? ORDER BY route_version',(trip_id,))],
            'candidates':[dict(r) for r in db.execute('SELECT id,base_version,requirements_json,result_json,progress_json,expires_at,consumed FROM city_engine_candidates WHERE trip_id=? AND expires_at>?',(trip_id,stamp()))]}
    for r in result['versions']:
        read_requirements(r['requirements_json']);read_itinerary(r['itinerary_json'])
    for r in result['candidates']:
        read_requirements(r['requirements_json']);read_result(r['result_json'])
    return result
