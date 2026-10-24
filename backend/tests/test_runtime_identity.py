from app.runtime import STARTUP, compare, source_fingerprint


def test_health_reports_startup_identity_without_settings(client):
    body = client.get('/api/health').json()
    assert body['status'] == 'ok'
    assert body['runtime'] == STARTUP
    assert set(body['runtime']) == {'checkout_id', 'backend_sha256', 'git_head', 'pid', 'started_at'}


def test_edit_add_remove_and_rename_change_source_identity(tmp_path):
    source = tmp_path / 'source.py'
    source.write_text('old')
    original = source_fingerprint(tmp_path)
    source.write_text('new')
    assert source_fingerprint(tmp_path) != original
    source.write_text('old')
    extra = tmp_path / 'extra.py'
    extra.write_text('other')
    assert source_fingerprint(tmp_path) != original
    extra.unlink()
    assert source_fingerprint(tmp_path) == original
    source.rename(tmp_path / 'renamed.py')
    assert source_fingerprint(tmp_path) != original


def test_current_stale_and_other_checkout_are_distinguishable():
    disk = {'checkout_id': 'a', 'backend_sha256': 'new', 'git_head': 'head'}
    assert compare(disk.copy(), disk) == ('current', [])
    assert compare(disk | {'backend_sha256': 'old'}, disk) == ('restart_required', ['backend_sha256'])
    assert compare(disk | {'checkout_id': 'b'}, disk) == ('restart_required', ['checkout_id'])
    assert compare(disk | {'git_head': 'old'}, disk) == ('restart_required', ['git_head'])
    assert compare(None, disk) == ('unknown', ['startup_identity_missing'])
