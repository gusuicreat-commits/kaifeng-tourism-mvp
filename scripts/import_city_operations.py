"""Explicit maintenance import of bounded travel/cost evidence; no map queries or LLM."""
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.city import facts
from app.city.engine.models import OperationalBundle
from app.city.engine.storage import init_engine,import_operations
from pydantic import ValidationError


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('file',type=Path)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    try:
        if not a.database.is_file():raise ValueError('请先初始化并选择已有项目数据库')
        if a.file.stat().st_size>10000000:raise ValueError('文件超过10MB')
        bundle=OperationalBundle.model_validate_json(a.file.read_text())
        memory=sqlite3.connect(':memory:');memory.row_factory=sqlite3.Row
        with sqlite3.connect(f'file:{a.database}?mode=ro',uri=True) as source:source.backup(memory)
        init_engine(memory);report=import_operations(memory,bundle,facts.records(memory));memory.close()
        if a.apply:
            with sqlite3.connect(a.database) as db:
                db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');init_engine(db);db.execute('BEGIN IMMEDIATE')
                report=import_operations(db,bundle,facts.records(db),apply=True)
        print(json.dumps(report,ensure_ascii=False,indent=2));return 0
    except ValidationError as e:
        print(json.dumps(e.errors(include_input=False,include_context=False,include_url=False),ensure_ascii=False),file=sys.stderr);return 1
    except (ValueError,OSError,sqlite3.Error) as e:
        print(str(e),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
