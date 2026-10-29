#!/usr/bin/env python3
"""Validate explicit FlyAI hotel -> existing city-place bindings; dry-run by default."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from fastapi import HTTPException
from app.city.hotel_binding import init, import_binding


def apply(db,bindings):
    init(db)
    for binding in bindings:import_binding(db,binding)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('json_file',type=Path,help='JSON 数组；字段见 LocationBinding')
    parser.add_argument('--database',type=Path,required=True,help='包含已有地点事实的 SQLite 库')
    parser.add_argument('--apply',action='store_true',help='明确写入；默认仅在内存副本核验')
    args=parser.parse_args()
    try:
        if args.json_file.stat().st_size>1_000_000:raise ValueError('绑定包不得超过 1MB')
        bindings=json.loads(args.json_file.read_text())
        if not isinstance(bindings,list) or not 1<=len(bindings)<=1000:raise ValueError('请提供 1–1000 项绑定的 JSON 数组')
        memory=sqlite3.connect(':memory:');memory.row_factory=sqlite3.Row
        with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro',uri=True) as source:source.backup(memory)
        try:apply(memory,bindings)
        finally:memory.close()
        if args.apply:
            with sqlite3.connect(args.database,timeout=15) as db:
                db.row_factory=sqlite3.Row
                db.execute('BEGIN IMMEDIATE')
                apply(db,bindings)
        print(json.dumps({'status':'applied' if args.apply else 'dry_run','bindings':len(bindings)},ensure_ascii=False))
        return 0
    except (ValueError,OSError,sqlite3.Error,HTTPException) as exc:
        print(json.dumps({'status':'rejected','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
