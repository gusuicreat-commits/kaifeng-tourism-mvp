import pytest
from postgres_guard import validate_test_database

URL = 'postgresql://scenic_test:local-only@127.0.0.1:5432/scenic_test'


def test_accepts_explicit_local_disposable_database():
    assert validate_test_database(URL, '1')['dbname'] == 'scenic_test'


@pytest.mark.parametrize('url,disposable,business', [
    (URL, None, None),
    (URL.replace('127.0.0.1', 'production.example'), '1', None),
    (URL.replace('/scenic_test', '/tourism'), '1', None),
    (URL.replace('scenic_test:local-only', 'postgres:local-only'), '1', None),
    (URL + '?hostaddr=192.0.2.1', '1', None),
    (URL + '?options=-csearch_path=business', '1', None),
    (URL, '1', URL),
    ('not a connection string', '1', None),
])
def test_rejects_unsafe_targets_before_connecting(url, disposable, business):
    with pytest.raises(ValueError):
        validate_test_database(url, disposable, business)


@pytest.mark.parametrize('key', ['PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS'])
def test_rejects_libpq_environment_overrides(monkeypatch, key):
    monkeypatch.setenv(key, 'unsafe-override')
    with pytest.raises(ValueError, match='overrides'):
        validate_test_database(URL, '1')
