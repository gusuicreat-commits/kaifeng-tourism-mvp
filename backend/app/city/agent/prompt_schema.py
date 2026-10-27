"""Compact JSON schemas for model prompts.

Exported contracts keep their full Pydantic annotations. Model prompts keep
types, enum choices and semantic descriptions while dropping presentation and
length metadata that does not help the model choose a value.
"""

from typing import Any


# Length bounds are enforced by Pydantic after the response; they add no
# semantic choice for the model. Pattern formats are described on their fields.
_DROP_KEYS = {'title', 'default', 'examples', 'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern'}


def _compact(value):
    if isinstance(value, dict):
        result = {k: _compact(v) for k, v in value.items() if k not in _DROP_KEYS}
        union = result.get('anyOf')
        if union and all(set(item) == {'type'} for item in union):
            result = {k: v for k, v in result.items() if k != 'anyOf'} | {
                'type': [item['type'] for item in union]
            }
        elif union and len(union) == 2 and union[-1] == {'type': 'null'} and union[0].get('type'):
            first = union[0]
            result = {k: v for k, v in result.items() if k != 'anyOf'} | first | {
                'type': [first['type'], 'null']
            }
            if 'enum' in result:
                result['enum'] = [*result['enum'], None]
        elif union and len(union) == 2 and union[-1] == {'type': 'null'} and set(union[0]) == {'enum'}:
            result = {k: v for k, v in result.items() if k != 'anyOf'} | {'enum': [*union[0]['enum'], None]}
        elif union and len(union) == 2 and union[-1] == {'type': 'null'} and set(union[0]) == {'const'}:
            result = {k: v for k, v in result.items() if k != 'anyOf'} | {'enum': [union[0]['const'], None]}
        # String literal choices already exclude every other type.
        if result.get('type') == 'string' and (
                'enum' in result and all(isinstance(v, str) for v in result['enum']) or
                isinstance(result.get('const'), str)):
            del result['type']
        elif result.get('type') == ['string', 'null'] and 'enum' in result and all(
                v is None or isinstance(v, str) for v in result['enum']):
            del result['type']
        return result
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


def _references(value, ref):
    if isinstance(value, dict):
        return int(value.get('$ref') == ref) + sum(_references(v, ref) for v in value.values())
    if isinstance(value, list):
        return sum(_references(v, ref) for v in value)
    return 0


def _inline(value, ref, definition):
    if isinstance(value, dict):
        if value.get('$ref') == ref:
            return definition | {k: v for k, v in value.items() if k != '$ref'}
        return {k: _inline(v, ref, definition) for k, v in value.items()}
    if isinstance(value, list):
        return [_inline(v, ref, definition) for v in value]
    return value


def prompt_schema(value: Any):
    result = _compact(value)
    # A definition used once costs a name and a reference without saving text.
    while isinstance(result, dict) and result.get('$defs'):
        for name, definition in result['$defs'].items():
            ref = '#/$defs/' + name
            if not _references(definition, ref) and _references(result, ref) == 1:
                del result['$defs'][name]
                result = _inline(result, ref, definition)
                break
        else:
            break
    if isinstance(result, dict) and result.get('$defs') == {}:
        del result['$defs']
    return _compact(result)
