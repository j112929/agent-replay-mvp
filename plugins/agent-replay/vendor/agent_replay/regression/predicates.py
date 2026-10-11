"""Bounded declarative predicates and a documented JSON schema subset; no eval."""
from ..serialization import MISSING, canonical

TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
         'integer': int, 'number': (int, float), 'null': type(None)}
SCHEMA_KEYS = {'type', 'properties', 'required', 'additionalProperties', 'items',
               'enum', 'const', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems'}


def validate_schema(schema, depth=0):
    if depth > 8 or not isinstance(schema, dict) or set(schema) - SCHEMA_KEYS:
        raise ValueError('Unsupported declarative JSON schema')
    if schema.get('type') not in TYPES: raise ValueError('Schema requires a supported type')
    for key in ('minimum', 'maximum'):
        if key in schema and type(schema[key]) not in (int, float): raise ValueError('Invalid schema bound')
    for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 10000): raise ValueError('Invalid schema size')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not 1 <= len(schema['enum']) <= 100): raise ValueError('Invalid schema enum')
    props = schema.get('properties', {})
    if not isinstance(props, dict) or len(props) > 100: raise ValueError('Invalid schema properties')
    for value in props.values(): validate_schema(value, depth + 1)
    required = schema.get('required', [])
    if not isinstance(required, list) or not all(isinstance(x, str) and x in props for x in required): raise ValueError('Invalid required properties')
    if 'additionalProperties' in schema and type(schema['additionalProperties']) is not bool: raise ValueError('Unsupported additionalProperties')
    if 'items' in schema: validate_schema(schema['items'], depth + 1)


def schema_matches(value, schema):
    cls = TYPES[schema['type']]
    if type(value) not in (cls if isinstance(cls, tuple) else (cls,)): return False
    if 'const' in schema and canonical(value) != canonical(schema['const']): return False
    if 'enum' in schema and not any(canonical(value) == canonical(x) for x in schema['enum']): return False
    if type(value) in (int, float):
        if 'minimum' in schema and value < schema['minimum']: return False
        if 'maximum' in schema and value > schema['maximum']: return False
    if isinstance(value, (str, list)):
        suffix = 'Length' if isinstance(value, str) else 'Items'
        if 'min' + suffix in schema and len(value) < schema['min' + suffix]: return False
        if 'max' + suffix in schema and len(value) > schema['max' + suffix]: return False
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])): return False
        if schema.get('additionalProperties') is False and set(value) - set(props): return False
        if not all(schema_matches(value[k], rule) for k, rule in props.items() if k in value): return False
    if isinstance(value, list) and 'items' in schema:
        return all(schema_matches(x, schema['items']) for x in value)
    return True


def validate_predicate(rule, depth=0):
    if depth > 8 or not isinstance(rule, dict): raise ValueError('Invalid declarative predicate')
    if set(rule) == {'all'} or set(rule) == {'any'}:
        items = rule.get('all', rule.get('any'))
        if not isinstance(items, list) or not 1 <= len(items) <= 20: raise ValueError('Invalid predicate group')
        for item in items: validate_predicate(item, depth + 1)
        return
    if set(rule) - {'path', 'op', 'value'} or rule.get('op') not in ('equals', 'exists', 'lte', 'gte', 'contains'):
        raise ValueError('Unsupported predicate operator')
    if not isinstance(rule.get('path'), str) or rule['path'] and not rule['path'].startswith('/'):
        raise ValueError('Predicate requires a JSON pointer')
    if rule['op'] != 'exists' and 'value' not in rule: raise ValueError('Predicate value required')
    if rule['op'] in ('lte', 'gte') and type(rule['value']) not in (int, float): raise ValueError('Numeric predicate required')


def predicate_matches(value, rule):
    from .assertions import pointer
    if 'all' in rule: return all(predicate_matches(value, r) for r in rule['all'])
    if 'any' in rule: return any(predicate_matches(value, r) for r in rule['any'])
    actual = pointer(value, rule['path']); expected = rule.get('value'); op = rule['op']
    if actual is MISSING: return False
    if op == 'exists': return True
    if op == 'equals': return canonical(actual) == canonical(expected)
    if op in ('lte', 'gte'):
        return type(actual) in (int, float) and (actual <= expected if op == 'lte' else actual >= expected)
    if op == 'contains':
        from .assertions import contains_value
        return contains_value(actual, expected)
    return False
