#!/usr/bin/env python3
"""Atomically import the team's restaurant CSV as reference facts, never supply edges."""
import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.db import database_path
from app.city import facts
from app.city.restaurant import price_range
from app.city.schema import TZ

HEADERS=('店名（勿改）','现有人均（参考）','菜系','口味标签（多个用、分隔，如：清淡、偏咸、甜口）','辣度（不辣/微辣/辣）',
         '招牌菜（多个用、分隔）','人均（元）','适合时段（早餐/午餐/晚餐/夜宵）','来源说明','来源链接或截图文件名','采集人','采集日期')


def multiple(value):
    parts=tuple(p.strip() for p in value.split('、'))
    if any(not p or len(p)>120 for p in parts):raise ValueError('多值须用顿号分隔且不能含空项或超长项')
    return tuple(dict.fromkeys(parts))


def bundle(csv_file,db):
    items=facts.records(db);updates=[];errors=[];seen=set()
    with csv_file.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        if tuple(reader.fieldnames or ())!=HEADERS:raise ValueError('CSV列名或顺序与餐厅口味模板不一致')
        for line,row in enumerate(reader,2):
            try:
                if None in row or any(v is None for v in row.values()):raise ValueError('CSV列数不正确')
                row={k:v.strip() for k,v in row.items()}
                name=row[HEADERS[0]]
                matches=[v for v in items.values() if isinstance(v,facts.Vendor)
                         and (p:=items.get(('place',v.place_id))) and (p.name==name or name in p.aliases)]
                if len(matches)!=1:raise ValueError('店名须精确匹配唯一已有商家（名称或别名），未匹配或存在歧义：'+name)
                vendor=matches[0]
                if vendor.fact_id in seen:raise ValueError('同一商家在整批中重复')
                seen.add(vendor.fact_id);patch={}
                for column,field in ((2,'cuisine'),(3,'taste_tags'),(5,'signature_dishes'),(7,'meal_periods')):
                    if row[HEADERS[column]]:patch[field]=row[HEADERS[column]] if field=='cuisine' else multiple(row[HEADERS[column]])
                if row[HEADERS[4]]:
                    spicy={'不辣':'none','微辣':'mild','辣':'hot'}
                    if row[HEADERS[4]] not in spicy:raise ValueError('辣度只能填写不辣/微辣/辣')
                    patch['spiciness']=spicy[row[HEADERS[4]]]
                if row[HEADERS[6]]:
                    prices=price_range(row[HEADERS[6]])
                    if prices is None:raise ValueError('人均须为非负金额或递增区间，最多两位小数')
                    amounts=[format(Decimal(p)/100,'f') for p in prices]
                    patch['price_band']=(amounts[0] if prices[0]==prices[1] else '-'.join(amounts))+'元/人'
                if not patch:continue
                if any(not row[HEADERS[c]] for c in (8,9,10,11)):raise ValueError('有资料时须填写来源说明、链接或截图、采集人和采集日期')
                collected=date.fromisoformat(row[HEADERS[11]])
                if collected>facts.now().astimezone(TZ).date():raise ValueError('采集日期不能来自未来')
                identity='restaurant_'+hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
                source=facts.Source(kind='source',fact_id=identity,name='餐厅资料：'+name,source_type='other',
                    locator=row[HEADERS[8]]+'；'+row[HEADERS[9]],publisher=row[HEADERS[10]],
                    url=row[HEADERS[9]] if row[HEADERS[9]].startswith(('http://','https://')) else None,
                    collected_at=datetime.combine(collected,time(),TZ),rights_status='internal_only',
                    planning_status='reference',planning_note='队员采集参考资料，尚未人工核实')
                data=vendor.model_dump()|patch|{'source_ids':tuple(dict.fromkeys((*vendor.source_ids,identity))),
                    'validation_status':'unverified','planning_status':'reference','planning_note':'队员采集餐厅参考资料，以店内为准',
                    'verified_at':None,'reviewer':None,'verification_method':None}
                if vendor.rights_status=='unknown':data['rights_status']='internal_only'
                candidate=facts.Vendor.model_validate(data)
                if candidate!=vendor:candidate=candidate.model_copy(update={'version':vendor.version+1})
                updates.extend((source,candidate))
            except ValueError as exc:errors.append(f'第{line}行：{exc}')
    if errors:raise ValueError('\n'.join(errors))
    return facts.FactBundle(records=updates)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv',type=Path);parser.add_argument('--database',type=Path,default=database_path())
    parser.add_argument('--dry-run',action='store_true');args=parser.parse_args(argv)
    try:
        if args.csv.stat().st_size>10_000_000:raise ValueError('CSV不得超过10MB')
        if args.dry_run:
            with sqlite3.connect(':memory:') as db:
                db.row_factory=sqlite3.Row
                with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro',uri=True) as source:source.backup(db)
                result=facts.import_bundle(bundle(args.csv,db),db)
        else:
            with sqlite3.connect(f'file:{args.database.resolve()}?mode=rw',uri=True,timeout=15) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
                result=facts.import_bundle(bundle(args.csv,db),db,apply=True)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'rejected','error':str(exc)},ensure_ascii=False),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
