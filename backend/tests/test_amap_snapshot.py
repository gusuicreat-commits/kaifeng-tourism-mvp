"""Snapshot transport numbers are synthetic solver parameters, never formal evidence."""
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest
from app.city.engine.amap import AmapProvider, Quote

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_snapshot_solver_consumes_numeric_values_without_certifying_navigation(monkeypatch):
    snapshot = load('amap_snapshot_experiment',monkeypatch)
    q = Quote('estimated', duration_seconds=600, distance_m=800)
    result = snapshot.snapshot_checks(dict(walking=q,driving=q,transit=q),dining_quotes=(q,q))
    for mode, case in result['modes'].items():
        assert case['status'] == 'feasible' and case['quote_numbers_consumed']
        assert case['required_visits_preserved'] and case['fixed_window_and_return_checked']
        assert case['insufficient_time_rejected'] and case['missing_transport_rejected']
        assert case['result_evidence']=='synthetic' and not case['navigation_certified']
    assert result['modes']['walking']['hard_walking_limit_rejected']
    assert result['optional_dining']['included']
    assert not result['database_accessed'] and not result['navigation_certified']
    assert '600' not in json.dumps(result) and '800' not in json.dumps(result)


def test_failed_quotes_do_not_become_solver_inputs(monkeypatch):
    module=load('amap_snapshot_experiment',monkeypatch)
    report=module.snapshot_checks({'walking':Quote('timeout_or_network')})
    assert report['modes']['walking']['status']=='NOT_TESTED'


def baseline():
    return {'mode':'live','real_requests':11,'elapsed_ms':1,'events':[
        {'status':'success','sequence':i+1} for i in range(11)],
        'capabilities':{},'cases':{},'status':'PARTIAL'}


def test_continuation_budget_is_aggregate_and_no_map_content_is_exported(monkeypatch):
    module=load('complete_amap_validation',monkeypatch)
    from evaluate_amap_capabilities import synthetic_transport
    ticks=iter(range(100,300,2))
    p=AmapProvider('FAKE-KEY',enabled=True,terms_reviewed=True,api_version='v5',retries=0,
                   max_requests=9,clock=lambda:next(ticks),transport=httpx.MockTransport(synthetic_transport))
    result=module.complete(baseline(),provider=p,pause=lambda _:None)
    p.close()
    assert result['real_requests']==20 and result['continuation']['requests']==9
    assert result['events'][-1]['sequence']==20
    assert result['capabilities']['poi_v5']['status']=='AVAILABLE'
    assert result['snapshot_experiment']['modes']['walking']['quote_numbers_consumed']
    assert result['snapshot_experiment']['optional_dining']['included']
    assert 'FAKE-KEY' not in json.dumps(result) and '114.3' not in json.dumps(result)
    assert 'TEST-Q' not in json.dumps(result)


def test_continuation_refuses_to_reset_exhausted_budget(monkeypatch):
    module=load('complete_amap_validation',monkeypatch)
    with pytest.raises(ValueError):module.complete(baseline()|{'real_requests':20})
    p=AmapProvider('FAKE',enabled=True,terms_reviewed=True,max_requests=10)
    with pytest.raises(ValueError):module.complete(baseline(),provider=p)
    p.close()


def test_continuation_records_attempts_even_when_snapshot_fails(monkeypatch):
    module=load('complete_amap_validation',monkeypatch)
    from evaluate_amap_capabilities import synthetic_transport
    def fail(*args,**kwargs):raise ValueError('untrusted URL?key=FAKE-KEY')
    monkeypatch.setattr(module,'snapshot_checks',fail)
    ticks=iter(range(100,300,2))
    p=AmapProvider('FAKE-KEY',enabled=True,terms_reviewed=True,api_version='v5',retries=0,
                   max_requests=9,clock=lambda:next(ticks),transport=httpx.MockTransport(synthetic_transport))
    report=module.complete(baseline(),provider=p,pause=lambda _:None);p.close()
    assert report['real_requests']==20 and report['followup_stop_reason']=='local_experiment_error'
    assert report['local_error_type']=='ValueError' and 'untrusted URL' not in json.dumps(report)
