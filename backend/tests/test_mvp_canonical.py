"""Superseded place aliases resolve to canonical records without reopening selection."""
from datetime import datetime, timedelta

from app.city import facts
from app.city.engine.context import PlanningContext
from app.city.engine.models import OperationalBundle, Requirements
from app.city.engine.prepare import prepare
from app.city.schema import TZ


def _place(fact_id, name, *, superseded_by=None, longitude=114.35, latitude=34.79):
    return facts.Place(
        kind='place', fact_id=fact_id, name=name, place_type='restaurant',
        source_ids=('src',), planning_status='reference', rights_status='internal_only',
        planning_note='合成测试', longitude=longitude, latitude=latitude,
        coordinate_system='GCJ02', superseded_by=superseded_by,
    )


def _context(*records):
    source = facts.Source(
        kind='source', fact_id='src', name='资料来源', source_type='other', locator='test',
        collected_at='2026-10-24T12:00:00+08:00', planning_status='reference',
        rights_status='internal_only', planning_note='合成测试',
    )
    items = {(r.kind, r.fact_id): r for r in (source, *records)}
    facts.check_links(items)
    assessed = datetime(2026, 10, 25, 9, tzinfo=TZ)
    return PlanningContext(items, OperationalBundle(), assessed)


def test_canonical_resolves_alias_and_prepare_rewrites_goal():
    alias = _place('diyilou_sihou', '第一楼（旧）', superseded_by='pl_wb_vendor_000001')
    canonical = _place('pl_wb_vendor_000001', '第一楼·总店')
    ctx = _context(alias, canonical)
    assert ctx.canonical('place', 'diyilou_sihou') == 'pl_wb_vendor_000001'
    assert ctx.resolve('place', 'diyilou_sihou') is canonical

    day = ctx.assessed_at.date()
    starts = ctx.assessed_at + timedelta(minutes=1)
    ends = ctx.assessed_at + timedelta(hours=3)
    req = Requirements.model_validate({
        'business_date': day, 'starts_at': starts, 'ends_at': ends,
        'origin_place_id': canonical.fact_id, 'destination_place_id': canonical.fact_id,
        'goals': [{'goal_id': 'g', 'target_kind': 'place', 'target_id': 'diyilou_sihou', 'kind': 'dining'}],
    })
    problem = prepare(req, ctx)
    assert all(a.place_id == canonical.fact_id for a in problem.activities)


def test_public_records_keeps_superseded_alias_but_marks_it_not_ready(monkeypatch):
    alias = _place('diyilou_sihou', '第一楼（旧）', superseded_by='pl_wb_vendor_000001')
    canonical = _place('pl_wb_vendor_000001', '第一楼·总店')
    items = {(r.kind, r.fact_id): r for r in (alias, canonical)}

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _sql, _params=()):
            return []

    monkeypatch.setattr(facts, 'connect', lambda: _DB())
    monkeypatch.setattr(facts, 'records', lambda db: dict(items))
    result = facts.public_records('place')
    ids = {r['record']['fact_id'] for r in result['items']}
    assert ids == {'diyilou_sihou', 'pl_wb_vendor_000001'}
    alias_row = next(r for r in result['items'] if r['record']['fact_id'] == 'diyilou_sihou')
    assert alias_row['record']['superseded_by'] == 'pl_wb_vendor_000001'
    assert not alias_row['ready']


def test_planning_visible_distinguishes_reference_from_candidate():
    reference = _place('pl_wb_vendor_000001', '第一楼·总店')
    alias = _place('diyilou_sihou', '第一楼（旧）', superseded_by='pl_wb_vendor_000001')
    assert facts.planning_visible(reference)
    assert not facts.planning_visible(alias)
    assert not facts.planning_visible(reference.model_copy(update={'planning_status': 'candidate'}))
    assert facts.planning_visible(reference.model_copy(update={'rights_status': 'permitted'}))


def test_alias_name_resolution_matches_canonical_place():
    reference = _place('pl_wb_vendor_000001', '第一楼·中华老字号（寺后街总店）')
    reference = reference.model_copy(update={'aliases': ('第一楼', '第一楼（寺后街店）')})
    other = _place('pl_wb_vendor_000002', '第一楼分店', longitude=114.36, latitude=34.80)
    items = {(r.kind, r.fact_id): r for r in (reference, other)}

    # Exact canonical name and a known alias both resolve; unrelated text does not.
    assert facts.place_by_name(items, '第一楼·中华老字号（寺后街总店）') is reference
    assert facts.place_by_name(items, '第一楼') is reference
    assert facts.place_by_name(items, '不存在的店') is None
    assert facts.name_matches(reference, '我想去第一楼吃饭')


def test_place_by_name_prefers_unique_located_record_when_names_collide():
    canonical = _place('pl_wb_000003', '开封府', longitude=114.3465, latitude=34.7897)
    legacy = _place('kaifengfu', '开封府', longitude=None, latitude=None)
    legacy = legacy.model_copy(update={'coordinate_system': 'unknown'})
    items = {(r.kind, r.fact_id): r for r in (canonical, legacy)}

    # Two same-name records: only one has usable coordinates, so it wins.
    assert facts.place_by_name(items, '开封府') is canonical

    # When two same-name records both carry coordinates, resolution is ambiguous.
    both_located = facts.place_by_name(
        {(r.kind, r.fact_id): r for r in (canonical, legacy.model_copy(
            update={'coordinate_system': 'GCJ02', 'longitude': 114.35, 'latitude': 34.79}))},
        '开封府',
    )
    assert both_located is None


def test_planning_choice_accepts_known_alias_when_canonical_name_absent(monkeypatch):
    from app.city.agent import planning_intent
    from app.city.agent.models import PlanningIntent, MaterialChoice

    canonical = _place('pl_wb_vendor_000001', '第一楼·中华老字号（寺后街总店）')
    canonical = canonical.model_copy(update={'aliases': ('第一楼', '第一楼（寺后街店）')})
    items = {(r.kind, r.fact_id): r for r in (canonical,)}

    envelope = type('E', (), {
        'identity': type('I', (), {
            'generated_at': datetime(2026, 10, 25, 9, tzinfo=TZ),
        })(),
    })()
    snapshot = type('S', (), {
        'planning': type('P', (), {'facts': items})(),
        'envelope': envelope,
    })()
    text = '再去第一楼吃灌汤包'
    plan = PlanningIntent(
        operation='update', quote=text,
        choices=(MaterialChoice(name='第一楼·中华老字号（寺后街总店）', priority='must', status='pending', quote='再去第一楼吃灌汤包'),),
    )
    # Should not raise: the quote carries a known alias for the canonical name.
    planning_intent.grounded(plan, text, snapshot)


def test_planning_drops_invalid_mode_constraint_before_validation():
    from app.city.agent.models import Understanding
    from app.city.agent.understand import _normalize_model_output
    raw = {
        'schema_version': 'travel-intent/3.3',
        'actions': [{'kind': 'modify', 'quote': '全程步行'}],
        'planning': {'operation': 'update', 'quote': '全程步行', 'fields': [
            {'field': 'mode', 'value': 'walking', 'quote': '全程步行'},
        ]},
        'constraints': [
            {'field': 'mode', 'strength': 'hard', 'value': 'walking', 'quote': '全程步行'},
            {'field': 'meal_at', 'strength': 'hard', 'value': '第一楼', 'quote': '第一楼吃灌汤包'},
            {'field': 'objective', 'strength': 'hard', 'value': '全程步行', 'quote': '全程步行'},
            {'field': 'return_by', 'strength': 'hard', 'value': '22:00', 'quote': '22:00'},
        ],
    }
    normalized = _normalize_model_output(raw)
    result = Understanding.model_validate(normalized)
    # Malformed constraints dropped; the valid return_by survives.
    assert [c.field for c in result.constraints] == ['return_by']
    assert result.planning.fields[0].field == 'mode'
