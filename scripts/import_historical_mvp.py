"""Convert the preserved 2026-10-20 collection into sourced MVP catalog rows.

Road edges, simulated observations, dated starts and media publication are not
imported here. Historic program times stay text and never become show sessions.
"""
import csv
from pathlib import Path

from scripts.import_team_workbook import base_fact, clip

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / '开封旅游数据采集_20261020'
PARKS = {'qingming': 'pl_wb_000001', 'wansui': 'pl_wb_000002', 'kaifengfu': 'pl_wb_000003',
         'longting': 'pl_wb_000004', 'tieta': 'pl_wb_000006'}


def rows(name):
    with (PACKAGE / 'tables' / (name + '.csv')).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def text(value):
    return str(value or '').strip() or None


def build():
    if not (PACKAGE / 'SHA256SUMS.txt').exists():
        return [], {'status': 'not_present', 'path': str(PACKAGE)}
    sources = rows('sources')
    source_by_id = {r['source_id']: r for r in sources}
    output = []
    for row in sources:
        if row['retrieval_status'] == 'failed':
            continue
        output.append({'kind': 'source', **base_fact('src_hist_' + row['source_id'], []),
            'name': '2026-10-20采集 · ' + row['source_id'], 'source_type': 'official' if 'osm' not in row['source_id'] else 'other',
            'url': text(row['source_url']), 'locator': str(PACKAGE / row['evidence_file']) if row['evidence_file'] else None,
            'publisher': '历史采集包', 'collected_at': row['checked_at']})

    def refs(*ids):
        return ['src_hist_' + x for x in dict.fromkeys(i for i in ids if i) if 'src_hist_' + x in {s['fact_id'] for s in output}]

    items = []
    place_index = {}
    for row in rows('pois'):
        name, park = text(row['name']), PARKS.get(row['scenic_id'])
        if not name or not park:
            continue
        stage = row['type'].startswith('performance_venue')
        kind = 'stage' if stage else 'restaurant' if row['type'] == 'amenity:restaurant' else 'poi'
        key = park, name, kind
        if key in place_index:
            continue
        fact_id = 'pl_hist_' + row['poi_id']
        record = {'kind': 'place', **base_fact(fact_id, refs()),
                  'name': clip(name, 120), 'place_type': kind, 'parent_place_id': park,
                  'notes': clip(row['quality_flag'], 2000)}
        if row['lat'] and row['lon'] and row['crs'] == 'EPSG:4326':
            record.update(latitude=float(row['lat']), longitude=float(row['lon']), coordinate_system='WGS84')
        # A source URL and collection date are retained even where sources.csv
        # used a different registry key for this row.
        sid = 'src_hist_poi_' + row['poi_id']
        record['source_ids'] = [sid]
        output.append({'kind': 'source', **base_fact(sid, []), 'name': '地点来源 · ' + name,
                       'source_type': 'other', 'url': text(row['source_url']), 'collected_at': row['checked_at']})
        if row['suggested_stay_min']:
            record['suggested_stay_min'] = int(float(row['suggested_stay_min']))
        place_index[key] = record
        items.append(record)
    output += items
    for row in rows('facilities'):
        park = PARKS.get(row['scenic_id'])
        if not park or not text(row['name']):
            continue
        identity = row['facility_id'] or row['scenic_id'] + '_row_' + str(len(output))
        sid = row['source_id']
        if not refs(sid):
            sid = 'facility_' + identity
            output.append({'kind': 'source', **base_fact('src_hist_' + sid, []),
                'name': '设施来源 · ' + row['name'], 'source_type': 'official', 'url': text(row['source_url']),
                'collected_at': row['checked_at']})
        output.append({'kind': 'facility', **base_fact('fac_hist_' + identity, refs(sid)),
                       'place_id': park, 'name': clip(row['name'], 120), 'category': clip(row['category'], 100),
                       'description': clip('；'.join(filter(None, [text(row['type']), text(row['location_text']), text(row['validity'])])), 2000)})
    for filename in ('qingming_programs', 'qingming_unresolved_shows', 'show_candidates_undated'):
        for row in rows(filename):
            park = PARKS.get(row['scenic_id'])
            if not park or not text(row['name']):
                continue
            raw_id = row.get('program_id') or row['candidate_id']
            sid = row.get('source_id')
            if not sid:
                sid = 'program_' + raw_id
                output.append({'kind': 'source', **base_fact('src_hist_' + sid, []),
                    'name': '节目来源 · ' + row['name'], 'source_type': 'official', 'url': text(row['source_url']),
                    'collected_at': row['checked_at']})
            output.append({'kind': 'program', **base_fact('prg_hist_' + raw_id, refs(sid)),
                           'place_id': park, 'name': clip(row['name'], 120), 'stage_name': clip(row.get('venue_name'), 200),
                           'schedule_text': clip(row.get('time_text'), 1000),
                           'notes': clip('；'.join(filter(None, [text(row.get('field_status')), text(row.get('quality_flag')), text(row.get('note'))])), 2000)})
    return output, {'status': 'converted', 'path': str(PACKAGE), 'records': len(output),
                    'excluded': ['osm道路与候选通行图', '模拟客流', 'show_session', '媒体公开转载', '无坐标无名POI']}
