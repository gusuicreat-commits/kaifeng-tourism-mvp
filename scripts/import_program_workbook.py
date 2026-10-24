#!/usr/bin/env python3
"""Extract scenic program rows and embedded photos from a supplied workbook."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SCENIC_IDS = {'万岁山武侠城': 'wansui', '清明上河园': 'qingming'}
NS = {
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'


def image_anchors(archive):
    rels = ET.fromstring(archive.read('xl/drawings/_rels/drawing1.xml.rels'))
    targets = {rel.get('Id'): rel.get('Target') for rel in rels}
    drawing = ET.fromstring(archive.read('xl/drawings/drawing1.xml'))
    output = {}
    for anchor in drawing:
        start = anchor.find('xdr:from', NS)
        picture = anchor.find('xdr:pic', NS)
        if start is None or picture is None:
            continue
        blip = picture.find('.//a:blip', NS)
        if blip is None:
            continue
        target = targets.get(blip.get(RID))
        if not target:
            continue
        row = int(start.find('xdr:row', NS).text) + 1
        output.setdefault(row, []).append('xl/' + target.removeprefix('../'))
    return output


def extract_image(archive, member, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        image.save(destination, 'JPEG', quality=84, optimize=True, progressive=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    args = parser.parse_args()
    records = []
    with zipfile.ZipFile(args.workbook) as archive:
        anchors = image_anchors(archive)
        sheet = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)['节目清单']
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            if not row[0]:
                continue
            scenic_name, name = str(row[1]).strip(), str(row[2]).strip()
            scenic_id = SCENIC_IDS.get(scenic_name)
            photos = []
            slug = f'{scenic_id or "unmatched"}-program-r{row_number:02d}'
            for index, member in enumerate(anchors.get(row_number, []), 1):
                filename = f'{slug}-{index}.jpg'
                extract_image(archive, member, ROOT / 'frontend/public/images/program-workbook' / filename)
                photos.append(f'/images/program-workbook/{filename}')
            records.append({
                'number': row[0], 'scenicName': scenic_name, 'scenicId': scenic_id,
                'name': name, 'tag': row[3] or '', 'venue': row[4] or '',
                'recommendedTime': row[5] or '', 'description': row[6] or '',
                'ticketTips': row[7] or '', 'photoNote': row[8] or '', 'photos': photos,
            })
    output = {
        'source': args.workbook.name,
        'sha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        'records': records,
    }
    target = ROOT / 'frontend/src/data/programWorkbook.generated.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(records)} programs and {sum(len(r["photos"]) for r in records)} embedded photos.')


if __name__ == '__main__':
    main()
