import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def isolated_memory_runtime(monkeypatch):
    from app.memory.session import _rates
    _rates.clear()
    from app import model_schedule
    import itertools
    ticks=itertools.count(0,100)
    monkeypatch.setattr(model_schedule,'_scheduler',model_schedule.ModelScheduler(clock=lambda:next(ticks)))
    monkeypatch.setenv('TOURISM_LLM_API_KEY','')


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.sqlite3'))
    monkeypatch.setenv('M7_DELETION_LEDGER_PATH',str(tmp_path/'deletions.sqlite3'))
    with TestClient(app) as c:
        yield c
