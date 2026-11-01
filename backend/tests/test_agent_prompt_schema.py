"""A7 prompt schemas keep semantic enum definitions and the input budget bounded."""
import inspect
import json
import copy
import re
from types import SimpleNamespace
from pathlib import Path
from typing import Literal, get_args, get_origin

from pydantic import BaseModel
import pytest

from app.city.agent import models as agent_models, service, understand
from app.city.agent.models import Decision, Understanding
from app.city.agent.prompt_schema import prompt_schema
from app.city.assistant import interpret, models as assistant_models
from test_city_assistant import city


def _literal_values(annotation):
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)
    if origin in (tuple, list, set, frozenset):
        return _literal_values(get_args(annotation)[0]) if get_args(annotation) else ()
    if origin is None:
        return ()
    values = []
    for item in get_args(annotation):
        values.extend(_literal_values(item))
    return tuple(values)


def _model_classes(module):
    return [value for _, value in inspect.getmembers(module, inspect.isclass)
            if issubclass(value, BaseModel) and value.__module__ == module.__name__]


def test_every_model_literal_option_is_defined_for_the_model():
    classes = _model_classes(agent_models) + _model_classes(assistant_models)
    missing = []
    for model in classes:
        for name, field in model.model_fields.items():
            values = _literal_values(field.annotation)
            if not values:
                continue
            description = field.description or ''
            for value in values:
                if str(value) not in description:
                    missing.append(f'{model.__name__}.{name}={value!r}')
    assert not missing, 'missing Literal definitions: ' + ', '.join(missing)


def _system_texts():
    return {
        'understanding': understand.understanding_system_prompt() + json.dumps(
            prompt_schema(Understanding.model_json_schema()), ensure_ascii=False, separators=(',', ':')),
        'react': service.decision_system_prompt(Decision.model_json_schema()),
        'assistant_v2': interpret.intent_system_prompt(),
    }


@pytest.mark.parametrize('call', ['understanding', 'react', 'assistant_v2'])
def test_previous_rules_reach_the_model_verbatim(call):
    rules = json.loads((Path(__file__).parent / 'fixtures' / 'prompt_rules_main.json').read_text())
    sent = re.sub(r'\s+', '', _system_texts()[call])
    missing = []
    for rule in rules[call]:
        if re.sub(r'\s+', '', rule) in sent:
            continue
        if any(re.sub(r'\s+', '', segment) not in sent for segment in rule.split('；')):
            missing.append(rule)
    assert not missing, f'{call} missing original rules:\n' + '\n'.join(missing)


def test_rule_map_names_the_actual_locations_without_exceptions():
    fixture = Path(__file__).parent / 'fixtures'
    rules = json.loads((fixture / 'prompt_rules_main.json').read_text())
    table = json.loads((fixture / 'agent_prompt_rule_map.json').read_text())
    assert table['exceptions'] == []
    assert [row['old'] for row in table['rules']] == [rule for entries in rules.values() for rule in entries]
    locations = {'understanding:' + name: text for name, text in understand.UNDERSTANDING_PROMPT_SECTIONS}
    locations.update(decision_system_prompt=_system_texts()['react'], intent_system_prompt=_system_texts()['assistant_v2'])
    for model in _model_classes(agent_models):
        locations.update({'schema:' + model.__name__ + '.' + name: field.description or ''
                          for name, field in model.model_fields.items()})
    for row in table['rules']:
        assert row['old'] in locations[row['location']], row


def test_only_understanding_uses_section_headings():
    sent = _system_texts()
    assert '【' in sent['understanding']
    for call in ('react', 'assistant_v2'):
        assert '【' not in sent[call] and '】' not in sent[call]


def test_shared_prompt_schema_keeps_descriptions_and_removes_presentation_keys():
    schema = prompt_schema(Understanding.model_json_schema())
    dumped = json.dumps(schema, ensure_ascii=False)
    assert 'title' not in dumped and 'default' not in dumped
    assert 'must=游客明确表示非去不可' in dumped and 'want=一般想去' in dumped


def test_cancel_is_only_for_pending_changes_and_place_rejections_use_standby():
    description=prompt_schema(Understanding.model_json_schema())['properties']['actions']['items']['properties']['kind']['description']
    # Claude review: phrasing it as "用planning.choices(status=standby)" made the live model
    # emit actions[].kind="planning.choices"; say plainly that it is not an action.
    assert 'cancel只撤销待确认修改' in description
    assert '出发前不去/候补/改优先级不是action，填planning.choices' in description
    assert description in _system_texts()['understanding']


def test_pending_preference_rules_reach_the_model():
    description=agent_models.PlanningField.model_fields['field'].description
    sent=_system_texts()['understanding']
    assert description in sent
    assert description.startswith('city/date;start_time/end_time;origin/destination;pace;objective;mode;budget_fen;trip_days;start_date/end_date;trip_nights。spice辣度none不辣/mild微辣/any能吃辣/都行；interest多排/avoid不排:shows演出/food美食/night夜游/scenic景区；美食是兴趣非新增菜品。')
    for meaning in (
        ('口语拒绝','→avoid'),
        ('夜间不出门/夜市','avoid:night','非end_time'),
        ('怕辣/辣的不行','spice:none','非constraints.food'),
        ('减少','不填avoid/interest'),
    ):
        assert all(word in description for word in meaning),meaning
    planning=dict(understand.UNDERSTANDING_PROMPT_SECTIONS)['planning']
    assert 'awaiting=preferences' in planning and '答偏好' in planning
    assert planning.replace('awaiting=preferences答偏好。','')=='【规划】只有单纯问两个地点之间怎么走/多久/多远/路线怎么走才给route_query；含多个停靠点、去A去B去C、出发并返回、全程步行等整趟游玩是planning，不是问路。地点抄原文，不发明ID或坐标，origin与use_current_position二选一。只有明确这里/当前位置才用当前位置，不因缺GPS阻止明确地点问路。天气给weather_query；查询不修改游玩日期，明确天气重排用planning.operation=weather_replan。planning单patch：fields填city城市、origin出发地、destination结束地、日期、HH:MM时间、budget_fen分。day_ref/scope=every_day据quote。constraints只放用户明确提出的要求，区分hard/soft/optional/unknown；模糊时间null/unknown。未提的偏好(节奏/目标/步行上限)不输出、不填默认值。走路/全程步行只是mode；walking_seconds仅在说“步行不超过X分钟”时给秒数。'
    assert '口味/辣度不是菜名，放planning.fields的spice' in sent


def test_schema_compaction_preserves_single_shared_and_recursive_definitions():
    source = {
        '$defs': {
            'Single': {'type': 'string', 'description': '原文规则。'},
            'Shared': {'type': 'integer', 'minimum': 1},
            'Recursive': {'type': 'object', 'properties': {'next': {'$ref': '#/$defs/Recursive'}}},
        },
        'type': 'object', 'additionalProperties': False, 'required': ['one'],
        'properties': {
            'one': {'$ref': '#/$defs/Single'}, 'two': {'$ref': '#/$defs/Shared'},
            'three': {'$ref': '#/$defs/Shared'}, 'tree': {'$ref': '#/$defs/Recursive'},
            'value': {'anyOf': [{'type': 'string'}, {'type': 'integer'}, {'type': 'null'}]},
            'choice': {'anyOf': [{'type': 'string', 'enum': ['x', 'y']}, {'type': 'null'}]},
            'scope': {'anyOf': [{'type': 'string', 'const': 'every_day'}, {'type': 'null'}]},
        },
    }
    original = copy.deepcopy(source)
    result = prompt_schema(source)
    assert source == original
    assert result['properties']['one'] == source['$defs']['Single']
    assert result['$defs'] == {k: v for k, v in source['$defs'].items() if k != 'Single'}
    assert result['properties']['value'] == {'type': ['string', 'integer', 'null']}
    assert result['properties']['choice'] == {'enum': ['x', 'y', None]}
    assert result['properties']['scope'] == {'enum': ['every_day', None]}
    assert result['additionalProperties'] is False and result['required'] == ['one']
    assert prompt_schema(result) == result


def test_every_dropped_pattern_has_a_field_format_in_the_sent_text():
    clock_pattern = r'^(?:[01]\d|2[0-3]):[0-5]\d$'
    id_pattern = r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$'
    id_format = 'ID格式：ASCII字母或数字开头，后续仅ASCII字母、数字、_、.、:、-。'
    expected = {('understanding', 'StateIntent', 'start_time'): (clock_pattern, 'HH:MM为24小时制，小时00–23、分钟00–59。')}
    expected.update({('assistant_v2', 'Intent', name): (id_pattern, id_format) for name in (
        'preserve_stop_ids', 'remove_stop_id', 'stop_id', 'place_id', 'add_place_id', 'add_food_id')})
    expected['assistant_v2', 'Reference', 'id'] = (id_pattern, id_format)
    found = {}

    def patterns(value):
        if isinstance(value, dict):
            if 'pattern' in value:
                yield value['pattern']
            for child in value.values():
                yield from patterns(child)
        elif isinstance(value, list):
            for child in value:
                yield from patterns(child)

    sent = _system_texts()
    for call, model in [('understanding', Understanding), ('react', Decision), ('assistant_v2', assistant_models.Intent)]:
        schema = model.model_json_schema()
        for name, definition in [(model.__name__, schema), *schema.get('$defs', {}).items()]:
            for field, value in definition.get('properties', {}).items():
                for pattern in patterns(value):
                    key = call, name, field
                    assert key in expected, f'Undocumented dropped pattern: {key} {pattern}'
                    explanation = expected[key][1]
                    assert explanation in value.get('description', '') and explanation in sent[call], key
                    found[key] = pattern, explanation
        assert not list(patterns(prompt_schema(schema)))
    assert found == expected


def test_maximum_real_context_stays_under_budget_for_all_three_calls(city, monkeypatch, capsys):
    """Use the shared synthetic city fixture at its context limits, without a live provider."""
    from test_city_assistant import planned
    from test_city_agent import snapshot

    planned(city)
    snap, _ = snapshot(city)
    snap.services.update(version=9, requirements={
        'category': 'hotels', 'nights': 3, 'guests': 4, 'rooms': 2,
        'room_type': '标准间', 'stay_budget_fen': 240000,
        'attributes': {'window': {'value': True, 'strength': 'hard'}, 'quiet': {'value': True, 'strength': 'soft'}},
    })
    snap.materials[:] = [
        {'name': f'候选地点{i}', 'priority': 'want', 'status': 'pending', 'fact_kind': 'place', 'fact_id': 'museum'}
        for i in range(12)
    ]
    snap.envelope = snap.envelope.model_copy(update={
        'recent_messages': [{'role': 'user', 'text': '历史对话' * 80} for _ in range(8)],
        'summary': {'text': '历史摘要' * 120, 'authoritative': False},
        'facts': [{'kind': 'place', 'id': f'place-{i}', 'name': '地点' * 20} for i in range(24)],
        'pending_changes': [{'candidate_id': f'candidate-{i}', 'status': 'valid', 'changes': ['调整' * 20]} for i in range(8)],
    })

    calls = []
    monkeypatch.setattr(understand.language, 'available', lambda: True)

    def fake_provider(system, payload, **kwargs):
        calls.append(('understanding' if '仅理解本轮' in system else 'react', len(system), len(json.dumps(payload, ensure_ascii=False))))
        return ({'conversation': {'reply': '收到。'}} if calls[-1][0] == 'understanding' else {'tool': 'finish'}), {'source': 'mock_model'}

    monkeypatch.setattr(service.provider, 'generate', fake_provider)
    understand.parse('你好', snap, allow_model=True)
    assert any(name == 'understanding' for name, *_ in calls), calls

    task = service.Task('a7-budget', 'session', 'trip', SimpleNamespace(), 'x', snap)
    task.intent = Understanding()
    task.observations = {'search_dining': {'status': 'ok', 'items': []}, 'query_shows': {'status': 'ok', 'items': []}}
    service._decision(task)

    captured = []
    monkeypatch.setattr(interpret.language, 'available', lambda: True)
    monkeypatch.setattr(interpret.language, '_call', lambda system, payload: captured.append((len(system), len(json.dumps(payload, ensure_ascii=False))) ) or {})
    interpret.model_intent('帮我看看行程', snap.envelope)
    calls.append(('assistant', *captured[-1]))

    from app.city.agent.provider import MODEL_INPUT_CHARACTERS
    # The V3 calls share the provider limit; the V2 assistant keeps its own 12000 envelope.
    assert all(system + payload <= (12000 if name == 'assistant' else MODEL_INPUT_CHARACTERS) for name, system, payload in calls), calls
    # 8400 是 2026-10-31 E23 新增互动模式字段后在 8000 基础上放宽的预留上限，硬限制是 provider.MODEL_INPUT_CHARACTERS。
    assert len(_system_texts()['understanding']) < 8400
    report = 'A7 prompt budget: ' + json.dumps({name: {'system': system, 'payload': payload, 'total': system + payload,
                                                     'remaining': (12000 if name == 'assistant' else MODEL_INPUT_CHARACTERS) - system - payload}
                                                for name, system, payload in calls}, ensure_ascii=False)
    with capsys.disabled():
        print(report)

