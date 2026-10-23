"""Explainable statistics. Demo and reported streams never mix; aggregates are not live readings."""
from datetime import datetime, timezone, timedelta
from statistics import mean
from zoneinfo import ZoneInfo
from .db import connect, network_get

TZ = ZoneInfo('Asia/Shanghai')
SOURCE_PRIORITY={'official_history':0,'official_summary':0,'field':1,'third_party':2}


def classify(level):
    if level is None:
        return 'unknown'
    return 'low' if level < .35 else 'medium' if level < .7 else 'high'


def normalized(row):
    if row['crowd_level'] is not None:
        return row['crowd_level']
    if row['count'] is not None and row['capacity']:
        return min(1, row['count']/row['capacity'])
    return None


def history_for(rows, now):
    buckets = {}
    for row in rows:
        when = datetime.fromisoformat(row['observed_at']).astimezone(TZ)
        end = datetime.fromisoformat(row['period_end']) if row['period_end'] else when
        # Long aggregation periods cannot tell us an intraday pattern.
        if end > now or (end-when).total_seconds()>7200 or (row['period_end'] and ((end-timedelta(microseconds=1)).astimezone(TZ).hour//2 != when.hour//2 or end.astimezone(TZ).date()!=when.date())):
            continue
        val=normalized(row)
        if val is None or row['confidence'] < .4:
            continue
        bucket=when.hour//2*2
        # Give each date equal weight, regardless of sampling frequency.
        daily=buckets.setdefault(bucket,{})
        priority=SOURCE_PRIORITY[row['source_type']]
        day=when.date().isoformat()
        if day not in daily or priority < daily[day]['priority']:
            daily[day]={'priority':priority,'values':[val], 'source_ids':[f'observation:{row["id"]}']}
        elif priority == daily[day]['priority']:
            daily[day]['values'].append(val)
            daily[day]['source_ids'].append(f'observation:{row["id"]}')
    return [dict(hour=h,label=f'{h:02d}:00–{h+2:02d}:00',level=round(mean(mean(v['values']) for v in days.values()),3),
                 category=classify(mean(mean(v['values']) for v in days.values())),sample_days=len(days),
                 sample_count=sum(len(v['values']) for v in days.values()),
                 source_ids=[s for v in days.values() for s in v['source_ids']]) for h,days in sorted(buckets.items())]


def crowd_summary(sid, dataset='demo', hour=13, now=None, target_time=None):
    now=now or datetime.now(timezone.utc)
    with connect() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM observations WHERE scenic_id=? AND is_simulated=? ORDER BY observed_at',
                                         (sid,1 if dataset=='demo' else 0))]
    # Use aware datetime sorting, not ISO text ordering across different timezones.
    cutoff=min(now,target_time) if target_time else now
    rows=sorted([r for r in rows if datetime.fromisoformat(r['observed_at'])<=cutoff], key=lambda r:datetime.fromisoformat(r['observed_at']))
    network=network_get(sid)
    nodes=[f['properties'] for f in network['features'] if f['geometry']['type']=='Point'] if network else []
    results=[]
    for node in nodes:
        subset=[r for r in rows if r['node_id']==node['id']]
        history=history_for(subset, min(now, target_time) if target_time else now)
        matching=next((b for b in history if b['hour']==hour//2*2 and b['sample_days']>=3),None)
        current=None
        if dataset=='reported':
            observations=[r for r in subset if r['measurement_kind']=='observation']
            eligible=[r for r in observations if 0<=(now-datetime.fromisoformat(r['observed_at'])).total_seconds()<=3600 and r['confidence']>=.4]
            last=min(eligible,key=lambda r:(SOURCE_PRIORITY[r['source_type']],-datetime.fromisoformat(r['observed_at']).timestamp())) if eligible else None
            if last:
                current=last | {'level':normalized(last),'category':classify(normalized(last)),
                                'label':'当前观测（导入记录，来源未经系统独立核实）'}
        estimate=None if matching is None else {
            'level':matching['level'],'category':matching['category'],'hour':hour,
            'sample_days':matching['sample_days'],'kind':'historical_estimate',
            'source_ids':matching['source_ids'], 'sample_count':matching['sample_count'],
            'label':'模拟历史估计' if dataset=='demo' else '历史估计（非实时）',
            'method':'同一两小时时段，同日同时段优先官方来源，再按日求均值并跨日平均；至少3个采样日；不混合演示与导入数据。'}
        # Off-time planning must not use a current observation from another hour.
        use_current=current if current and now.astimezone(TZ).hour//2==hour//2 else None
        if target_time and (target_time.astimezone(TZ).date()!=now.astimezone(TZ).date()
                            or abs((target_time-now).total_seconds())>3600):
            use_current=None
        effective=use_current or estimate
        results.append(dict(node_id=node['id'],name=node['name'],current=current,estimate=estimate,history=history,
            level=effective['level'] if effective else None,category=classify(effective['level'] if effective else None),
            basis='current_observation' if use_current else 'historical_estimate' if estimate else 'unknown',
            source_ids=[f'observation:{use_current["id"]}'] if use_current else estimate['source_ids'] if estimate else [],
            evidence_quality='limited' if effective else 'unavailable',
            latest=subset[-1] if subset else None,is_simulated=dataset=='demo'))
    return dict(scenic_id=sid,dataset=dataset,is_simulated=dataset=='demo',generated_at=now.isoformat(),hour=hour,
        notice='模拟数据，用于功能验证，不表示现场客流。' if dataset=='demo' else '导入记录的来源与可信度由提交者声明；缺少近期观测时当前客流显示未知。',
        nodes=results)
