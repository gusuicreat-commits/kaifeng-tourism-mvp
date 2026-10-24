#!/usr/bin/env python3
"""Extract video link records from the workbook media-assets sheet."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SCENIC_IDS = {
    'pl_000001': 'qingming', 'pl_000002': 'wansui', 'pl_000003': 'kaifengfu',
    'pl_000004': 'longting', 'pl_000006': 'tieta',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    args = parser.parse_args()
    ws = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)['22_media_assets']
    rows = list(ws.iter_rows(values_only=True))
    headers = [str(value).strip() if value else '' for value in rows[2]]
    records = []
    for row in rows[3:]:
        record = dict(zip(headers, row))
        if record.get('media_type') != 'video' or not record.get('original_url'):
            continue
        records.append({
            'mediaId': record['media_id'],
            'placeId': record.get('place_id'),
            'scenicId': SCENIC_IDS.get(record.get('place_id')),
            'title': record.get('title') or '视频资料',
            'description': record.get('description') or '',
            'url': record['original_url'],
            'sourcePageUrl': record.get('source_page_url'),
            'publisher': record.get('publisher') or '',
            'rightsStatus': record.get('rights_status') or 'unknown',
            'validationStatus': record.get('validation_status') or 'candidate',
        })
    output = {
        'source': args.workbook.name,
        'sha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        'records': records,
    }
    target = ROOT / 'frontend/src/data/scenicVideoAssets.generated.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    matched = sum(1 for item in records if item['scenicId'])
    print(f'Imported {len(records)} video links; matched {matched} current scenic pages.')


if __name__ == '__main__':
    main()
