#!/usr/bin/env python3
"""Validate a city-facts/1 bundle; explicitly apply to a selected SQLite database."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from pydantic import ValidationError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.city.facts import FactBundle, init_catalog, import_bundle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('json_file',type=Path,nargs='?')
    parser.add_argument('--schema',action='store_true',help='输出JSON Schema，不读写数据库')
    parser.add_argument('--database',type=Path,help='显式选择运行库；未提供时在内存空库验证')
    parser.add_argument('--apply',action='store_true',help='原子导入，默认只校验')
    args=parser.parse_args()
    if args.schema:
        print(json.dumps(FactBundle.model_json_schema(),ensure_ascii=False,indent=2));return 0
    if not args.json_file:parser.error('请提供待核验的数据包')
    if args.apply and not args.database:parser.error('--apply 必须显式选择 --database')
    try:
        if args.json_file.stat().st_size>10_000_000:raise ValueError('单个数据包不得超过10MB')
        bundle=FactBundle.model_validate_json(args.json_file.read_text())
        # Always validate on a consistent in-memory copy first, even in dry-run.
        memory=sqlite3.connect(':memory:');memory.row_factory=sqlite3.Row
        if args.database and args.database.exists():
            with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro',uri=True) as source:source.backup(memory)
        init_catalog(memory)
        result=import_bundle(bundle,memory)
        memory.close()
        if args.apply:
            args.database.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(args.database,timeout=15) as db:
                db.row_factory=sqlite3.Row
                init_catalog(db)
                db.execute('BEGIN IMMEDIATE')
                result=import_bundle(bundle,db,apply=True)  # Recheck versions under the write lock.
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except ValidationError as exc:
        print(json.dumps({'status':'rejected','errors':exc.errors(include_input=False,include_context=False,include_url=False)},ensure_ascii=False),file=sys.stderr)
        return 1
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'rejected','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
