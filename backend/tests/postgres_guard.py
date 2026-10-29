"""Fail closed before destructive scenic fixtures can connect."""
import os

from psycopg.conninfo import conninfo_to_dict


def validate_test_database(url, disposable, business_url=None):
    if any(os.getenv(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
        raise ValueError('Remove libpq environment overrides before destructive tests')
    if disposable != '1':
        raise ValueError('Set SCENIC_TEST_DATABASE_DISPOSABLE=1 only for a disposable database')
    try:
        params = conninfo_to_dict(url)
    except Exception:
        raise ValueError('Invalid isolated PostgreSQL test URL') from None
    # No libpq options/service/hostaddr overrides, remote hosts or ambient credentials.
    if (set(params) - {'host', 'port', 'dbname', 'user', 'password'}
            or params.get('host') not in {'127.0.0.1', 'localhost', '::1'}
            or params.get('dbname') != 'scenic_test'
            or params.get('user') != 'scenic_test'
            or not params.get('password')
            or business_url):
        raise ValueError('PostgreSQL tests require local scenic_test user/database and no business database URL')
    return params
