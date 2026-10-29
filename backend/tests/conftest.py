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
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','')
    # app.main loads the developer's .env; tests that need demo mode or a live provider opt in explicitly.
    for name in ('KAIFENG_DEMO_MODE','AMAP_LOCATION_SEARCH_ENABLED','AMAP_REFERENCE_ROUTES_ENABLED','QWEATHER_ENABLED','FLYAI_ENABLED'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('SCENIC_ADMIN_API_KEY', 'test-admin-key-012345678901234567890123456789')


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.sqlite3'))
    monkeypatch.setenv('M7_DELETION_LEDGER_PATH',str(tmp_path/'deletions.sqlite3'))
    with TestClient(app) as c:
        yield c


def pytest_addoption(parser):
    parser.addoption('--require-postgres', action='store_true',
                     help='Fail instead of skipping when the disposable PostgreSQL database is absent')


def pytest_configure(config):
    import os
    from postgres_guard import validate_test_database
    url = os.environ.get('SCENIC_TEST_DATABASE_URL')
    if not url:
        if config.getoption('--require-postgres'):
            raise pytest.UsageError('--require-postgres requires SCENIC_TEST_DATABASE_URL')
        return
    try:
        validate_test_database(url, os.environ.get('SCENIC_TEST_DATABASE_DISPOSABLE'),
                               os.environ.get('SCENIC_SCHEDULE_DATABASE_URL'))
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None


@pytest.fixture(autouse=True)
def require_isolated_postgres(request):
    import os
    if request.node.get_closest_marker('postgres') and not os.environ.get('SCENIC_TEST_DATABASE_URL'):
        pytest.skip('PostgreSQL not executed: set disposable local SCENIC_TEST_DATABASE_URL; CI requires it')
