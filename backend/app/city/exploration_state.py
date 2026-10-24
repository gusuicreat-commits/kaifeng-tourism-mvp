"""Small persisted planning workspace; never an alternative formal itinerary."""
import json
from fastapi import HTTPException
from ..memory.storage import encoded, stamp


def read(db, trip_id):
    row = db.execute('SELECT * FROM city_exploration_states WHERE trip_id=?', (str(trip_id),)).fetchone()
    if row:
        return {'version':row['version'],'constraints':json.loads(row['constraints_json']),
                'result':json.loads(row['result_json']) if row['result_json'] else None,'rounds':row['rounds']}
    # Keep API and Agent reads consistent for pre-workspace planning receipts.
    # This read does not migrate, replay or write any old request.
    legacy=db.execute('SELECT result_json FROM city_receipts WHERE trip_id=? AND scope=? ORDER BY created_at DESC LIMIT 1',
        (str(trip_id),'exploration:'+str(trip_id))).fetchone()
    constraints=json.loads(legacy['result_json']).get('constraints') if legacy else None
    return {'version':0,'constraints':constraints,'result':None,'rounds':0}


def guard(db, trip_id, expected):
    if read(db, trip_id)['version'] != expected:
        raise HTTPException(409, detail={'code':'PLANNING_CHANGED',
            'message':'规划条件或协商状态已变化，请重新读取并计算。'})


def save(db, trip_id, constraints, result, rounds):
    version = read(db, trip_id)['version'] + 1
    if result is not None:
        result.update(planning_version=version, rounds=rounds)
    db.execute('''INSERT INTO city_exploration_states VALUES (?,?,?,?,?,?)
        ON CONFLICT(trip_id) DO UPDATE SET version=excluded.version,
        constraints_json=excluded.constraints_json,result_json=excluded.result_json,
        rounds=excluded.rounds,updated_at=excluded.updated_at''',
        (str(trip_id),version,encoded(constraints),encoded(result) if result is not None else None,rounds,stamp()))
    return read(db, trip_id)
