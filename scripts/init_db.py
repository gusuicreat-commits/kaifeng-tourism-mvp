"""Initialize without deleting or overwriting existing records."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from app.seed import seed
from app.db import connect, database_path
seed()
with connect() as db:
    db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, result TEXT NOT NULL)')
    counts={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['scenic','networks','observations','plans']}
print(f'Database: {database_path()}')
print(counts)
print('初始客流全为模拟；重复运行不覆盖已有数据。')
