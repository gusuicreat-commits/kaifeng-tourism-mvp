#!/usr/bin/env python3
"""Map supplied Wansui image links to the local detail gallery, preserving status."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SCENIC_IDS = {'pl_000001': 'qingming', 'pl_000002': 'wansui', 'pl_000003': 'kaifengfu',
              'pl_000004': 'longting', 'pl_000006': 'tieta'}


def records(sheet):
    rows = list(sheet.values)
    return [dict(zip(rows[2], row)) for row in rows[3:]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    args = parser.parse_args()
    workbook = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    assets = {row['media_id']: row for row in records(workbook['22_media_assets'])}
    images = []
    for relation in records(workbook['23_media_relations']):
        if relation.get('target_table') not in ('10_places', '24_highlights'):
            continue
        media = assets.get(relation.get('media_id'), {})
        if media.get('media_type') != 'image' or media.get('content_role') == 'map' or relation.get('relation_role') == 'map_evidence':
            continue
        if not str(media.get('original_url', '')).startswith('https://'):
            continue
        images.append({
            'mediaId': media['media_id'], 'title': media['title'],
            'placeId': relation.get('target_record_id'), 'scenicId': SCENIC_IDS.get(relation.get('target_record_id')),
            'targetTable': relation['target_table'],
            'url': media['original_url'], 'source': media.get('credit_line'),
            'sourcePageUrl': media.get('source_page_url'), 'author': media.get('creator'),
            'order': relation.get('sort_order') or 0,
            'rightsStatus': media.get('rights_status'),
            'publicationStatus': media.get('publication_status'),
            'validationStatus': media.get('validation_status'),
            'usageScope': media.get('usage_scope'), 'notes': media.get('notes'),
        })
    output = {
        'source': args.workbook.name,
        'sha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        'records': sorted(images, key=lambda item: item['order']),
    }
    target = ROOT / 'frontend/src/data/scenicMedia.generated.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f'Mapped {len(images)} scenic and highlight image links; maps excluded; source status preserved.')


if __name__ == '__main__':
    main()
