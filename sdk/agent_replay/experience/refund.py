"""Trusted, key-free refund simulator and qualification witnesses.

Executable code is installed with the package and selected only by the compiled
project registry. Scenario artifacts contain JSON data, never generated code.
"""
import copy
from ..capture import _active, tool
from ..serialization import digest

VERSION = 'refund-simulator.v1'


def reset(inputs):
    environment = copy.deepcopy(inputs['_environment'])
    return {'initial': copy.deepcopy(environment['initial_state']),
            'orders': copy.deepcopy(environment['initial_state']['orders']),
            'ledger': [], 'lookup_calls': 0, 'environment_digest': digest(environment)}


def snapshot(state):
    return {'adapter_version': VERSION, 'environment_digest': state['environment_digest'],
            'initial': copy.deepcopy(state['initial']),
            'orders': copy.deepcopy(state['orders']), 'ledger': copy.deepcopy(state['ledger'])}


def cleanup(state):
    state.clear()


def state():
    run = _active.get()
    if run is None or not hasattr(run, 'state'):
        raise RuntimeError('Refund environment requires a registered reset adapter')
    return run.state


def lookup_local(order_id):
    current = state()
    current['lookup_calls'] += 1
    if current['initial'].get('lookup_transient') and current['lookup_calls'] == 1:
        raise TimeoutError('Temporary lookup outage')
    if current['lookup_calls'] == 1 and current['initial'].get('lookup_not_found'):
        return {'order_id': order_id, 'found': False}
    order = current['orders'].get(order_id)
    if order is None:
        raise ValueError('Order not found')
    result = {'order_id': order_id, **copy.deepcopy(order)}
    if current['lookup_calls'] == 1 and current['initial'].get('lookup_partial'):
        result.pop('approved_minor')
    return result


def refund_local(order_id, amount_minor):
    current = state()
    if order_id not in current['orders'] or type(amount_minor) is not int or amount_minor <= 0:
        raise ValueError('Invalid refund')
    # Deliberately does not enforce approval: the verifier must catch agent bugs.
    current['ledger'].append({'order_id': order_id, 'amount_minor': amount_minor})
    current['orders'][order_id]['refunded_minor'] += amount_minor
    return {'ok': True, 'order_id': order_id, 'amount_minor': amount_minor}


@tool(name='orders.lookup', boundary_id='orders.lookup', side_effect='read')
def lookup(order_id):
    return lookup_local(order_id)


@tool(name='payments.refund', boundary_id='payments.refund', side_effect='write')
def payment(order_id, amount_minor):
    return refund_local(order_id, amount_minor)


def positive(run):
    """Independent constructive witness, separate from the candidate implementation."""
    inputs = run.input
    _retry(inputs['order_id'])
    expected = inputs['_environment']['initial_state']['witness_amount_minor']
    return payment(inputs['order_id'], expected)


def _retry(order_id):
    try:
        result = lookup(order_id)
    except TimeoutError:
        result = {}
    if not all(k in result for k in ('order_id', 'approved_minor', 'paid_minor', 'refunded_minor')):
        result = lookup(order_id)
    if not all(k in result for k in ('order_id', 'approved_minor', 'paid_minor', 'refunded_minor')):
        raise ValueError('Incomplete order response after retry')
    return result


def fixed(run):
    order = _retry(run.input['order_id'])
    amount = min(run.input['requested_minor'], order['approved_minor'],
                 order['paid_minor'] - order['refunded_minor'])
    return payment(order['order_id'], amount)


def broken(run):
    order = _retry(run.input['order_id'])
    return payment(order['order_id'], order['paid_minor'])


def wrong_entity(run):
    _retry(run.input['order_id'])
    return payment('other-order', run.input['_environment']['initial_state']['witness_amount_minor'])


def excess(run):
    order = _retry(run.input['order_id'])
    return payment(order['order_id'], order['approved_minor'] + order['paid_minor'] + 1)


def skipped(run):
    _retry(run.input['order_id'])
    return {'ok': False}


def duplicate(run):
    positive(run)
    return payment(run.input['order_id'], run.input['_environment']['initial_state']['witness_amount_minor'])


def partial(run):
    order = _retry(run.input['order_id'])
    return payment(order['order_id'], max(1, run.input['_environment']['initial_state']['witness_amount_minor'] - 1))


def misleading(run):
    _retry(run.input['order_id'])
    return {'ok': True, 'order_id': run.input['order_id'], 'amount_minor': run.input['_environment']['initial_state']['witness_amount_minor']}


def partial_execution(run):
    positive(run)
    raise RuntimeError('Execution failed after payment')


CANDIDATES = ('positive', 'fixed', 'broken', 'wrong_entity', 'excess', 'skipped',
              'duplicate', 'partial', 'misleading', 'partial_execution')
NEGATIVES = ('wrong_entity', 'excess', 'skipped', 'duplicate', 'partial', 'misleading', 'partial_execution')


def registry():
    prefix = 'agent_replay.experience.refund:'
    return {'schema_version': '1.0', 'project_id': 'refund-experience',
            'entrypoints': {name: {'callable': prefix + name} for name in CANDIDATES},
            'tools': {'refund-lookup': {'callable': prefix + 'lookup_local'},
                      'refund-payment': {'callable': prefix + 'refund_local'}},
            'state_adapters': {name: {'callable': prefix + fn} for name, fn in
                               [('refund-reset', 'reset'), ('refund-snapshot', 'snapshot'), ('refund-cleanup', 'cleanup')]},
            'error_mappings': {'TimeoutError': 'TimeoutError', 'ValueError': 'ValueError', 'RuntimeError': 'RuntimeError'}}
