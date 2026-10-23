"""Read-only Overpass coverage probe. Never imports roads or certifies walkability."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx

QUERY = '''[out:json][timeout:25];
area["name"="开封市"]["boundary"="administrative"]->.city;
nwr(area.city)["name"~"万岁山|清明上河园"];
out tags center;'''

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只读核查景区OSM对象与候选内部道路，不修改项目数据库')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', default='https://overpass-api.de/api/interpreter')
    args = parser.parse_args()
    report = {'checked_at':datetime.now(timezone.utc).isoformat(), 'endpoint':args.endpoint,
              'query':QUERY, 'license':'OSM data ODbL 1.0; attribution required',
              'limitations':['对象/道路数量不证明完整覆盖或现场可通行；需逐段GIS与实地核验。',
                             '不得据本探针将示意路网标记为真实道路。'], 'sites':[]}
    try:
        with httpx.Client(timeout=40.0) as client:
            response = client.post(args.endpoint, data={'data':QUERY})
            response.raise_for_status()
            elements = response.json()['elements']
            report['matched_objects'] = elements
            for obj in elements:
                if obj['type'] not in ('way','relation'):
                    continue
                area_id = obj['id']+(2400000000 if obj['type']=='way' else 3600000000)
                query = f'[out:json][timeout:15];area({area_id})->.site;way(area.site)["highway"];out tags geom;'
                roads = client.post(args.endpoint, data={'data':query})
                roads.raise_for_status()
                report['sites'].append({'name':obj.get('tags',{}).get('name'), 'osm_type':obj['type'],
                                        'osm_id':obj['id'], 'candidate_roads':roads.json()['elements']})
            report['status'] = 'queried_not_verified'
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        report['status'] = 'unavailable'
        report['error_type'] = type(exc).__name__
        if isinstance(exc,httpx.HTTPStatusError):
            report['http_status'] = exc.response.status_code
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'status':report['status'], 'matched_objects':len(report.get('matched_objects',[])),
                      'candidate_areas':len(report['sites']), 'output':str(args.output)}, ensure_ascii=False))
