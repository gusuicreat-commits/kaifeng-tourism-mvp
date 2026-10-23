"""CSV adapter. Future API adapters can call validate_and_store with the same canonical records."""
import io
from datetime import datetime, timezone
import pandas as pd
from fastapi import HTTPException
from pydantic import ValidationError
from .db import connect, network_get
from .models import Observation

REQUIRED={'scenic_id','node_id','observed_at','source_type','source_name','measurement_kind','confidence','is_simulated'}
OPTIONAL={'period_end','count','crowd_level','capacity','source_url'}


def validate_and_store(records):
    validated=[]
    errors=[]
    known={}
    keys=set()
    now=datetime.now(timezone.utc)
    for i,record in enumerate(records,2):
        try:
            item=Observation.model_validate(record)
            if item.scenic_id not in known:
                net=network_get(item.scenic_id)
                known[item.scenic_id]={f['properties']['id'] for f in net['features'] if f['geometry']['type']=='Point'} if net else set()
            if item.node_id not in known[item.scenic_id]:
                raise ValueError('景区或点位不存在；请使用内部地图的 node_id')
            if item.observed_at>now or (item.period_end and item.period_end>now):
                raise ValueError('不接受未来观测；预测应走独立模型接口')
            key=(item.scenic_id,item.node_id,item.observed_at.isoformat(),item.source_type,item.source_name,item.measurement_kind,item.is_simulated)
            if key in keys:
                raise ValueError('同一文件包含重复观测标识')
            keys.add(key)
            validated.append(item)
        except (ValidationError,ValueError) as exc:
            msg='; '.join(e['msg'] for e in exc.errors()) if isinstance(exc,ValidationError) else str(exc)
            errors.append({'row':i,'message':msg})
    if errors:
        raise HTTPException(422,detail={'message':'校验失败，未写入任何行','errors':errors[:50]})
    inserted=updated=0
    with connect() as db:
        for item in validated:
            data=item.model_dump(mode='json')
            data['is_simulated']=int(item.is_simulated)
            data['updated_at']=now.isoformat()
            existing=db.execute('''SELECT id FROM observations WHERE scenic_id=? AND node_id=? AND observed_at=?
                AND source_type=? AND source_name=? AND measurement_kind=? AND is_simulated=?''',
                (item.scenic_id,item.node_id,data['observed_at'],item.source_type,item.source_name,item.measurement_kind,int(item.is_simulated))).fetchone()
            if existing:
                updated+=1
                fields=[k for k in data]
                db.execute('UPDATE observations SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?', [data[k] for k in fields]+[existing['id']])
            else:
                inserted+=1
                db.execute('INSERT INTO observations ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',list(data.values()))
    return {'inserted':inserted,'updated':updated,'total':len(validated),'notice':'来源声明不会自动变成官方认证；演示标记已保留。'}


def import_csv(content):
    try:
        table=pd.read_csv(io.BytesIO(content),encoding='utf-8-sig',dtype=str,keep_default_na=False)
    except Exception:
        raise HTTPException(422,detail='CSV解析失败，请使用UTF-8编码与英文表头')
    if not REQUIRED.issubset(table.columns) or set(table.columns)-REQUIRED-OPTIONAL:
        raise HTTPException(422,detail=f'必需表头：{", ".join(sorted(REQUIRED))}；可选：{", ".join(sorted(OPTIONAL))}')
    if len(table)==0 or len(table)>5000:
        raise HTTPException(422,detail='每次需导入1–5000行')
    records=[]
    for raw in table.to_dict('records'):
        records.append({k:(None if v=='' and k in OPTIONAL else v) for k,v in raw.items()})
    return validate_and_store(records)
