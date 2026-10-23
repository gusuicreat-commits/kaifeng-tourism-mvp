"""Prepare a NEW restored database; never overwrite a running or existing database."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    args=parser.parse_args()
    if not args.source.is_file() or not args.ledger.is_file():parser.error('备份与当前删除台账均须存在')
    if args.destination.exists():parser.error('目标已存在；拒绝覆盖')
    args.destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with sqlite3.connect(f'file:{args.source}?mode=ro',uri=True) as source,sqlite3.connect(args.destination) as target:
            source.backup(target)
        os.environ['DATABASE_PATH']=str(args.destination.resolve())
        os.environ['M7_DELETION_LEDGER_PATH']=str(args.ledger.resolve())
        from app.memory.storage import init_memory, maintenance
        init_memory()  # Additive migrations let pre-city backups pass the same deletion gate.
        result=maintenance()
        print('恢复候选库已应用删除台账与TTL。尚未切换运行库：',args.destination)
        print(result)
    except Exception:
        args.destination.unlink(missing_ok=True)
        raise

if __name__=='__main__':main()
