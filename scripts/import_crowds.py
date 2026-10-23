"""Developer-only CSV import. No data management controls in visitor pages."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from app.seed import seed
from app.importer import import_csv
from fastapi import HTTPException

parser=argparse.ArgumentParser(description='导入客流CSV（开发维护工具）')
parser.add_argument('file',type=Path)
args=parser.parse_args()
try:
    with args.file.open('rb') as source:
        content=source.read(1024*1024+1)
    if len(content)>1024*1024:
        raise ValueError('CSV不能超过1 MiB')
    seed()
    print(json.dumps(import_csv(content),ensure_ascii=False,indent=2))
except (HTTPException,OSError,ValueError) as exc:
    print(json.dumps({'error':exc.detail if isinstance(exc,HTTPException) else str(exc)},ensure_ascii=False),file=sys.stderr)
    sys.exit(1)
