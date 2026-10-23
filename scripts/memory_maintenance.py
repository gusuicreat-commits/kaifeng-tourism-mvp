"""Run retention and deletion replay. Safe to schedule externally; no raw content printed."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.memory.storage import maintenance
if __name__=='__main__':
    import json
    print(json.dumps(maintenance(),ensure_ascii=False))
