#!/usr/bin/env python3
"""Extract the supplied food workbook into display-only frontend content.

This is a presentation import, not a city-facts verification or database import.
Run with the project virtualenv (openpyxl and Pillow required).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
FOOD_IDS = {
    '宫廷杏仁茶': 'xingrentea', '开封花生糕': 'huashenggao',
    '炒凉粉': 'chaoliangfen', '开封灌汤包': 'xiaolongbao',
    '桶子鸡': 'tongziji', '羊肉炕馍': 'yangroukangmo',
    '鲤鱼焙面': 'liyubeimian', '四味菜': 'siweicai',
}
NS = {
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'


def image_anchors(archive):
    relationships = ET.fromstring(archive.read('xl/drawings/_rels/drawing1.xml.rels'))
    targets = {rel.get('Id'): rel.get('Target') for rel in relationships}
    drawing = ET.fromstring(archive.read('xl/drawings/drawing1.xml'))
    output = {}
    for anchor in drawing:
        start = anchor.find('xdr:from', NS)
        picture = anchor.find('xdr:pic', NS)
        if start is None or picture is None:
            continue
        row = int(start.find('xdr:row', NS).text) + 1
        column = int(start.find('xdr:col', NS).text) + 1
        target = targets[picture.find('.//a:blip', NS).get(RID)]
        output.setdefault((row, column), []).append('xl/' + target.removeprefix('../'))
    return output


def convert(archive, member, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        image.save(destination, 'JPEG', quality=86, optimize=True, progressive=True)


def field(info, label):
    match = re.search(rf'(?:^|\n){label}[：:]\s*([^\n]*)', info or '')
    return match.group(1).strip() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    foods = []
    with zipfile.ZipFile(args.workbook) as archive:
        anchors = image_anchors(archive)
        sheet = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)['美食资料总表']
        current = None
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            name, intro, video, _, vendor, _, info = row
            if name:
                if name not in FOOD_IDS:
                    raise ValueError(f'未配置美食 ID：{name}')
                current = {'id': FOOD_IDS[name], 'intro': intro.strip(),
                           'video': video.strip() if video else None, 'photos': [], 'vendors': []}
                foods.append(current)
            if current is None or not vendor:
                raise ValueError(f'第 {row_number} 行缺少美食或商家对应关系')
            slug = f'{current["id"]}-r{row_number:02d}'
            dish_photos = []
            for index, member in enumerate(anchors.get((row_number, 4), []), 1):
                filename = f'{slug}-dish-{index}.jpg'
                convert(archive, member, ROOT / 'frontend/public/images/food-workbook' / filename)
                dish_photos.append(f'/images/food-workbook/{filename}')
            current['photos'].extend(dish_photos)
            shop_photos = []
            for index, member in enumerate(anchors.get((row_number, 6), []), 1):
                filename = f'{slug}-shop-{index}.jpg'
                convert(archive, member, ROOT / 'frontend/public/images/food-workbook' / filename)
                shop_photos.append(f'/images/food-workbook/{filename}')
            address = field(info, '地址')
            first_line = (info or '').split('\n')[0].strip()
            if not address and first_line and not re.search(r'[：:]', first_line):
                address = first_line
            current['vendors'].append({
                'id': slug, 'name': vendor.strip(), 'address': address,
                'hours': field(info, '营业时间'), 'perCapita': field(info, '人均'),
                'dishPrice': field(info, '美食价格'), 'photos': shop_photos,
                'dishPhotos': dish_photos,
            })
    output = {'source': args.workbook.name, 'sha256': digest,
              'status': '团队资料整理，商家信息与供应关系待复核', 'foods': foods}
    target = ROOT / 'frontend/src/data/foodWorkbook.generated.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(foods)} foods and {sum(len(f["vendors"]) for f in foods)} vendor rows.')


if __name__ == '__main__':
    main()
