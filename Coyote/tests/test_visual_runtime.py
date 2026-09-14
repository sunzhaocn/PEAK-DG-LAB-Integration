"""Visual event regressions against the final bootstrap-installed backend."""
import unittest

from review_support import run_isolated


FIXTURE = r'''
from copy import deepcopy
from unittest.mock import patch

EXT.extension_settings.update(respawn_guard_enabled=False, global_guard_enabled=False)
for cfg in B.rules.values():
    cfg['enabled'] = False
B.master_output_enabled = True
B.get_slot_id = lambda: 'review-slot'
original_rpc = B.send_rpc
original_graph_send = V._send_graph
rpc_calls = []
B.send_rpc = lambda method, data=None: (rpc_calls.append((method, deepcopy(data))) or True, 'recorded')
visual_calls = []
V._send_graph = lambda graph, output, *args: visual_calls.append((graph['id'], output['id'])) or True

def packet(**fields):
    result = dict(hasCharacter=True, localPlayer=True, dead=False, passedOut=False,
                  hp=100, staminaCurrent=1, staminaMax=1, speed=0,
                  scene='level', heldItem={'name': ''}, inventory={'backpackItems': []})
    result.update(fields)
    return result

def graph(gid, key, params=None, disabled=False):
    nodes = [dict(id='event', type='trigger', params={'rule_key': key, **(params or {})}),
             dict(id='out', type='output', params={'mode': 'repeat', 'cooldown': 0})]
    if disabled:
        nodes.append(dict(id='disable', type='disable_builtin', params={}))
    return V._normalize(dict(id=gid, name=gid, enabled=True, nodes=nodes,
                            links=[dict(**{'from': 'event', 'out': 'value', 'to': 'out', 'in': 'in'})]))

def install(*items):
    ok, message = V.save_graphs(list(items))
    assert ok, message
    V.runtime.clear()
    visual_calls.clear()
    rpc_calls.clear()
    B.last_trigger_time.clear()

def dispatch(previous, current):
    visual_calls.clear()
    B.previous_peak, B.latest_peak = previous, current
    B.handle_game_rules(current, previous)
    return list(visual_calls)
'''


class VisualRuntimeTests(unittest.TestCase):
    def run_case(self, script):
        run_isolated(FIXTURE + script)

    def test_independent_thresholds_coexist_with_unchanged_builtin_config(self):
        self.run_case(r'''
B.rules['speedAbove'].update(enabled=True, speed_threshold=1, cooldown=0)
before_rules = deepcopy(B.rules)
install(graph('low', 'speedAbove', {'speed_threshold': 3}),
        graph('high', 'speedAbove', {'speed_threshold': 8}))
assert dispatch(packet(speed=0), packet(speed=4)) == [('low', 'out')]
assert rpc_calls, 'the enabled built-in rule must still output at its own threshold'
assert dispatch(packet(speed=4), packet(speed=9)) == [('high', 'out')]
assert B.rules == before_rules, 'visual evaluation must never mutate B.rules'

# Missing node parameters use stable detector defaults, not live built-in edits.
install(graph('default', 'speedAbove'))
B.rules['speedAbove']['speed_threshold'] = 100
assert dispatch(packet(speed=0), packet(speed=6)) == [('default', 'out')]
''')

    def test_disabled_builtin_does_not_disable_visual_consumption_or_filters(self):
        self.run_case(r'''
for takeover in (False, True):
    B.rules['consumedItem'].update(enabled=False, item_filter='banana')
    before_rules = deepcopy(B.rules)
    install(graph('apple', 'consumedItem', {'item_filter': 'apple'}, takeover),
            graph('banana', 'consumedItem', {'item_filter': 'banana'}))
    previous = packet(lastConsumedItem={'id': '1', 'item': 'apple'})
    current = packet(lastConsumedItem={'id': '2', 'item': 'apple'})
    assert dispatch(previous, current) == [('apple', 'out')], takeover
    assert not rpc_calls, 'disabled built-ins must not be revived'
    assert dispatch(current, current) == []
    assert B.rules == before_rules

# Nodes in the same graph also keep their own item filters.
g = graph('two-nodes', 'consumedItem', {'item_filter': 'apple'})
g['nodes'].extend([dict(id='banana', type='trigger', params={'rule_key': 'consumedItem', 'item_filter': 'banana'}),
                   dict(id='out-b', type='output', params={'mode': 'repeat', 'cooldown': 0})])
g['links'].append({'from': 'banana', 'out': 'value', 'to': 'out-b', 'in': 'in'})
install(g)
assert dispatch(packet(lastConsumedItem={'id': '1'}),
                packet(lastConsumedItem={'id': '2', 'item': 'banana'})) == [('two-nodes', 'out-b')]
''')

    def test_single_recovery_and_threshold_state_are_private_per_node(self):
        self.run_case(r'''
for takeover in (False, True):
    for key, field, scale in [('hpRecover', 'hp', 1), ('staminaRecover', 'staminaCurrent', 100)]:
        EXT._reset_extension_runtime()
        before_runtime = deepcopy(EXT._RECOVERY_RUNTIME)
        install(graph('small', key, {'trigger_delta': 1, 'trigger_mode': 'single'}, takeover),
                graph('large', key, {'trigger_delta': 5, 'trigger_mode': 'single'}))
        prev = packet(**{field: 50 / scale})
        expected = [[('small', 'out')], [], [('large', 'out')]]
        for value, events in zip((52, 54, 56), expected):
            cur = packet(**{field: value / scale})
            assert dispatch(prev, cur) == events, (takeover, key, value)
            prev = cur
        assert EXT._RECOVERY_RUNTIME == before_runtime, 'visual recovery must not modify built-in state'
        assert dispatch(prev, prev) == []  # A plateau ends contiguous recovery.
        assert dispatch(prev, packet(**{field: 58 / scale})) == [('small', 'out')]

# Repeat emits after its threshold while the separate single node remains consumed.
install(graph('single', 'hpRecover', {'trigger_delta': 1, 'trigger_mode': 'single'}, True),
        graph('repeat', 'hpRecover', {'trigger_delta': 1, 'trigger_mode': 'repeat'}))
assert dispatch(packet(hp=50), packet(hp=52)) == [('single', 'out'), ('repeat', 'out')]
assert dispatch(packet(hp=52), packet(hp=54)) == [('repeat', 'out')]

# An enabled built-in recovery keeps its different threshold and own fired flag.
EXT._reset_extension_runtime()
B.rules['hpRecover'].update(enabled=True, trigger_delta=10, trigger_mode='single', cooldown=0)
install(graph('small', 'hpRecover', {'trigger_delta': 1, 'trigger_mode': 'single'}))
assert dispatch(packet(hp=50), packet(hp=52)) == [('small', 'out')]
assert EXT._RECOVERY_RUNTIME['hpRecover'] == {'active': True, 'start': 50.0, 'fired': False}
assert dispatch(packet(hp=52), packet(hp=61)) == []
assert EXT._RECOVERY_RUNTIME['hpRecover'] == {'active': True, 'start': 50.0, 'fired': True}
assert rpc_calls, 'the independent built-in recovery should output at 10 percent'
''')

    def test_area_entry_dwell_and_single_state_are_private(self):
        self.run_case(r'''
zone = dict(name='test', scene='level', x=0, y=0, z=0, radius=2)
other_zone = dict(zone, x=20)
install(graph('enter', 'areaEnter', {'area_zones': [zone]}, True),
        graph('other', 'areaEnter', {'area_zones': [other_zone]}),
        graph('short', 'areaDwell', {'area_zones': [zone], 'area_dwell_seconds': 1, 'trigger_mode': 'single'}),
        graph('long', 'areaDwell', {'area_zones': [zone], 'area_dwell_seconds': 3, 'trigger_mode': 'single'}))
before_runtime = deepcopy(EXT._AREA_RUNTIME)
inside = packet(position=dict(x=0, y=0, z=0))
outside = packet(position=dict(x=10, y=0, z=0))
with patch.object(bootstrap.VIS_INTEGRATION.time, 'monotonic', return_value=10):
    assert dispatch(outside, inside) == [('enter', 'out')]
with patch.object(bootstrap.VIS_INTEGRATION.time, 'monotonic', return_value=12):
    assert dispatch(inside, inside) == [('short', 'out')]
with patch.object(bootstrap.VIS_INTEGRATION.time, 'monotonic', return_value=14):
    assert dispatch(inside, inside) == [('long', 'out')]
with patch.object(bootstrap.VIS_INTEGRATION.time, 'monotonic', return_value=16):
    assert dispatch(inside, inside) == []
    assert dispatch(inside, outside) == []
with patch.object(bootstrap.VIS_INTEGRATION.time, 'monotonic', return_value=17):
    assert dispatch(outside, inside) == [('enter', 'out')]
assert EXT._AREA_RUNTIME == before_runtime, 'visual areas must not modify built-in state'
''')

    def test_existing_stateless_event_families_and_old_jump_packets(self):
        self.run_case(r'''
cases = [
    ('hp', {}, packet(hp=100), packet(hp=90)),
    ('staminaUse', {'trigger_delta': 10}, packet(staminaCurrent=1), packet(staminaCurrent=.8)),
    ('speedBelow', {'speed_threshold': 3}, packet(speed=5), packet(speed=2)),
    ('jump', {}, packet(jumpSeq=1), packet(jumpSeq=2)),
    ('jump', {}, packet(grounded=True), packet(grounded=False, velocity={'y': 1})),
    ('climbStart', {}, packet(climbing=False), packet(climbing=True)),
    ('crouchStart', {}, packet(crouching=False), packet(crouching=True)),
    ('heldItem', {'item_filter': 'apple'}, packet(), packet(heldItem={'name': 'Apple'})),
    ('backpackItem', {'item_filter': 'apple'}, packet(inventory={'backpackItems': ['Apple']}),
     packet(inventory={'backpackItems': ['Apple', 'Apple']})),
    ('heldState', {'item_filter': 'apple'}, packet(), packet(heldItem={'name': 'Apple'})),
    ('backpackState', {'item_filter': 'apple'}, packet(), packet(inventory={'backpackItems': ['Apple']})),
    ('statusRecover', {'trigger_delta': 5}, packet(statusNames=['Cold', 'Hunger'], statuses=[.3, .1]),
     packet(statusNames=['Hunger', 'Cold'], statuses=[.1, .2])),
]
for key, _, index, _ in B.RULE_META:
    if index is not None:
        cases.append((key, {}, packet(statusNames=[key], statuses=[.1]), packet(statusNames=[key], statuses=[.2])))
for key, params, previous, current in cases:
    install(graph('event', key, params, True))
    assert dispatch(previous, current) == [('event', 'out')], key
    assert dispatch(current, current) == [], key
''')

    def test_transition_filters_and_custom_incapacity_policy_remain_distinct(self):
        self.run_case(r'''
install(graph('hp', 'hp', disabled=True))
assert dispatch(packet(hp=100, scene='old'), packet(hp=20, scene='new')) == []
assert dispatch(packet(hp=100, characterInstanceId=1), packet(hp=20, characterInstanceId=2)) == []
assert dispatch(packet(hp=100, packetSeq=5), packet(hp=20, packetSeq=5)) == []
assert dispatch(packet(hp=100), packet(hp=20, localPlayer=False)) == []
assert dispatch(packet(hp=100, hasCharacter=False), packet(hp=20)) == []

# Stable incapacitation must not implicitly suppress a custom event.
assert dispatch(packet(hp=100, passedOut=True), packet(hp=20, passedOut=True)) == [('hp', 'out')]
g = graph('guarded', 'hp', disabled=True)
g['nodes'].append(dict(id='guard', type='telemetry', params={'custom_guard': 'active'}))
g['links'].append({'from': 'guard', 'out': 'value', 'to': 'out', 'in': 'in'})
install(g)
assert dispatch(packet(hp=100, passedOut=True), packet(hp=20, passedOut=True)) == []
''')

    def test_visual_rpc_sequence_keeps_one_cancellable_output_token(self):
        self.run_case(r'''
B.send_rpc = original_rpc
V._send_graph = original_graph_send
messages = []
class FakeSocket:
    def send(self, text):
        messages.append(json.loads(text))
        if len(messages) == 1:
            B.cancel_pending_output()
B.dg_ws = FakeSocket()
B.dg.update(app_id='review-client', slot_id='review-slot')
install(graph('hp', 'hp', disabled=True))
dispatch(packet(hp=100), packet(hp=90))
assert len(messages) == 1, 'Stop after intensity must prevent waveform/B-channel continuation'
assert messages[0]['data']['data']['t'] == 4

# Cancelling during calculations must block all RPCs for the old telemetry event.
messages.clear()
calculate = B.calculate_rule_intensities
def cancel_during_calculation(*args, **kwargs):
    B.cancel_pending_output()
    return calculate(*args, **kwargs)
B.calculate_rule_intensities = cancel_during_calculation
dispatch(packet(hp=90), packet(hp=80))
assert not messages
assert B.current_output_token() is None, 'dispatch must restore the calling thread context'
''')

    def test_incapacity_clear_does_not_revive_a_cancelled_dispatch(self):
        self.run_case(r'''
B.send_rpc = original_rpc
V._send_graph = original_graph_send
messages = []
class FakeSocket:
    def send(self, text):
        messages.append(json.loads(text))
B.dg_ws = FakeSocket()
B.dg.update(app_id='review-client', slot_id='review-slot')
special = V._example_special('death', 'death')
special['enabled'] = True
install(special)
dispatch(packet(dead=False), packet(dead=True))
assert any(item['data']['data'].get('t') == 4 for item in messages), 'normal death still permits its special output'

messages.clear()
V.runtime.clear()
incapacitated = B.peak_is_incapacitated
cancelled = False
def stop_before_death_clear(packet=None):
    global cancelled
    result = incapacitated(packet)
    if result and not cancelled:
        cancelled = True
        B.cancel_pending_output()
    return result
B.peak_is_incapacitated = stop_before_death_clear
dispatch(packet(dead=False), packet(dead=True))
assert not any(item['data']['data'].get('t') in (0, 4) for item in messages), 'old death event must stay cancelled'
''')


if __name__ == '__main__':
    unittest.main()
