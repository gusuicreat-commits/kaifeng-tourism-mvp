"""Local maintenance only: python scripts/import_schedules.py path/to/verified-facts.json"""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from app.db import init_db
from app.schedules import store_facts

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='导入带来源、有效日期和核验状态的演出/开放时间，不生成任何景区事实')
    parser.add_argument('json_file', type=Path)
    args = parser.parse_args()
    init_db()
    print(json.dumps(store_facts(json.loads(args.json_file.read_text())), ensure_ascii=False))
