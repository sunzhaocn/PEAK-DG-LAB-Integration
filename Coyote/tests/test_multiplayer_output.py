"""Player and route cancellation regressions; all transport is in memory."""
import unittest

from review_support import run_isolated


SETUP = r'''
import types

wire = []
class Socket:
    def send(self, message):
        wire.append(json.loads(message))
B.dg_ws = Socket()
B.master_output_enabled = True
B.latest_peak = {'hasCharacter': True, 'hp': 100, 'dead': False, 'passedOut': False}
for client in ('old', 'new', 'remote'):
    MP._update_multi_device_data(client, {
        'ev': 'devices.snapshot', 'devices': [{
            'slotId': 'same-slot', 'name': client,
            'slotState': {'hasDevice': True},
            'props': {'connectState': 'connected'},
        }],
    })
assert B.dg['app_id'] == 'old'

def packet(hp=100, **flags):
    return {'scene': 'test', 'players': [dict(playerId='p', hp=hp, **flags)]}

MP._handle_multiplayer_packet(packet())
assert MP.bind_player_device('p', 'remote', 'same-slot')[0]
assert MP.set_player_binding_enabled('p', True)[0]
MP.set_remote_output_enabled(True)
B.rules['hp'].update(
    enabled=True, cooldown=0, intensity_a=80, intensity_b=0,
    random_intensity=False, spike_enabled=False, thresholds=[],
    ramp_enabled=False, ramp_duration_ms=1000, ramp_steps=4,
    play_time_a=5000, play_time_b=5000,
)
wire.clear()

def operations():
    return [frame for frame in wire if frame['data']['m'] == 'device.op']

def clears():
    return [frame for frame in wire if frame['data']['m'] == 'device.op.clear']

class Clock:
    def __init__(self, callback=None):
        self.value = 1000.0
        self.callback = callback
    def time(self): return self.value
    def monotonic(self): return self.value
    def sleep(self, seconds):
        self.value += seconds
        if self.callback is not None:
            callback, self.callback = self.callback, None
            callback()

queued = []
class DelayedThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args
    def start(self): queued.append(self)
    def run(self): self.target(*self.args)
MP.threading = types.SimpleNamespace(Thread=DelayedThread)
'''


class MultiplayerOutputTests(unittest.TestCase):
    def test_terminal_state_clears_existing_output_once(self):
        run_isolated(SETUP + r'''
for state in ('dead', 'passedOut', 'fullyPassedOut', 'missing'):
    MP._handle_multiplayer_packet(packet())
    MP._handle_multiplayer_packet(packet(hp=80))
    assert operations(), state
    before = len(clears())
    terminal = {'players': []} if state == 'missing' else packet(hp=0, **{state: True})
    MP._handle_multiplayer_packet(terminal)
    assert len(clears()) == before + 1, (state, wire)
    assert clears()[-1]['clientId'] == 'remote'
    MP._handle_multiplayer_packet(terminal)
    assert len(clears()) == before + 1, state
    assert MP.get_player_bindings()['p']['enabled']
    wire.clear()
''')

    def test_remote_ramp_stops_at_death_and_does_not_revive_after_recovery(self):
        run_isolated(SETUP + r'''
B.rules['hp']['ramp_enabled'] = True
MP.time = Clock()
MP._handle_multiplayer_packet(packet(hp=80))
assert len(queued) == 1
worker = queued.pop()
MP.time.callback = lambda: MP._handle_multiplayer_packet(packet(hp=0, dead=True))
worker.run()
strengths = [x['data']['data']['v'] for x in operations() if x['data']['data']['t'] == 4]
assert strengths == [0], wire
assert len(clears()) == 1
MP._handle_multiplayer_packet(packet(hp=90))
wire.clear()
worker.run()  # A delayed old thread remains cancelled after the player recovers.
assert not operations()
MP._handle_multiplayer_packet(packet(hp=80))
assert len(queued) == 1
queued.pop().run()
assert any(x['data']['data'].get('t') == 4 and x['data']['data']['v'] > 0 for x in operations())
''')

    def test_death_between_preparation_and_send_rejects_delayed_ramp_write(self):
        run_isolated(SETUP + r'''
B.rules['hp']['ramp_enabled'] = True
MP._handle_multiplayer_packet(packet(hp=80))
worker = queued.pop()
prepared = threading.Event()
release = threading.Event()
original_send = B.send_payload
def paused_send(payload, **kwargs):
    data = payload.get('data') or {}
    if payload.get('m') == 'device.op' and data.get('t') == 4 and data.get('v', 0) > 0:
        prepared.set()
        assert release.wait(3), 'test did not release pending write'
    return original_send(payload, **kwargs)
B.send_payload = paused_send
MP.time = Clock()
thread = threading.Thread(target=worker.run)
thread.start()
assert prepared.wait(3), 'ramp did not reach the send boundary'
MP._handle_multiplayer_packet(packet(hp=0, passedOut=True))
clear_index = len(wire) - 1
assert wire[clear_index]['data']['m'] == 'device.op.clear'
release.set()
thread.join(3)
assert not thread.is_alive(), 'cancellation/send lock deadlock'
assert len(wire) == clear_index + 1, wire
''')

    def test_switch_stops_old_route_and_invalidates_only_local_work(self):
        run_isolated(SETUP + r'''
local_token = B.capture_output_token()
remote_token = B.capture_output_token(client_id='remote', slot_id='same-slot')
B.manual_session.update(active=True, generation=12, a={'configured_duration': -1}, b=None)
assert B.send_rpc('device.op', {'s': 'same-slot', 'c': 0, 't': 4, 'v': 50, 'd': 60000})[0]
assert MP.set_default_device('new', 'same-slot')[0]
assert not B.manual_session['active']
assert B.manual_session['generation'] > 12
assert [(x['clientId'], x['data']['data']['s']) for x in clears()] == [('old', 'same-slot')]
assert not B.is_output_token_current(local_token)
assert B.is_output_token_current(remote_token)
with B.output_context(local_token):
    assert not B.send_rpc('device.op', {'s': 'same-slot', 'c': 0, 't': 4, 'v': 80, 'd': 1000})[0]
assert MP.set_default_device('old', 'same-slot')[0]
assert not B.is_output_token_current(local_token), 'switching back revived old work'
with B.output_context(remote_token):
    assert MP.send_rpc_to_client('remote', 'device.op', {'s': 'same-slot', 'c': 0, 't': 4, 'v': 20, 'd': 1000})[0]
''')

    def test_local_ramp_pins_full_route_and_cannot_start_after_switch(self):
        run_isolated(SETUP + r'''
EXT._RAMP_GENERATION = 1
token = B.capture_output_token()
def switch():
    assert MP.set_default_device('new', 'same-slot')[0]
    assert MP.set_default_device('old', 'same-slot')[0]
EXT.time = Clock(switch)
EXT._ramp_worker(B.send_rpc, 1, 'same-slot', 0, 80, 5000, 1000, 4, token)
strengths = [(x['clientId'], x['data']['data']['v']) for x in operations()]
assert strengths == [('old', 0)], wire
wire.clear()
EXT._ramp_worker(B.send_rpc, 1, 'same-slot', 0, 80, 5000, 1000, 4, token)
assert not operations(), 'cancelled worker sent its initial zero operation'
fresh_token = B.capture_output_token()
EXT._ramp_worker(B.send_rpc, 1, 'same-slot', 0, 80, 5000, 1000, 4, fresh_token)
assert operations()[-1]['data']['data']['v'] == 80
assert all(x['clientId'] == 'old' for x in operations())
''')

    def test_route_stays_selected_when_clear_cannot_be_sent(self):
        run_isolated(SETUP + r'''
class FailedSocket:
    def send(self, message): raise OSError('simulated write failure')
B.dg_ws = FailedSocket()
ok, _ = MP.set_default_device('new', 'same-slot')
assert not ok
assert MP._default_route_snapshot()['client_id'] == 'old'
assert B.dg['app_id'] == 'old'
''')

    def test_device_reconnect_does_not_revive_local_ramp(self):
        run_isolated(SETUP + r'''
EXT._RAMP_GENERATION = 1
token = B.capture_output_token()
remote_token = B.capture_output_token(client_id='remote', slot_id='same-slot')
for online in (False, True):
    MP._update_multi_device_data('old', {
        'ev': 'devices.snapshot', 'devices': [{
            'slotId': 'same-slot', 'name': 'old',
            'slotState': {'hasDevice': online},
            'props': {'connectState': 'connected' if online else 'disconnected'},
        }],
    })
assert not B.is_output_token_current(token)
assert B.is_output_token_current(remote_token)
EXT.time = Clock()
wire.clear()
EXT._ramp_worker(B.send_rpc, 1, 'same-slot', 0, 80, 5000, 1000, 4, token)
assert not operations()
''')

    def test_app_reconnect_with_same_identity_does_not_revive_local_work(self):
        run_isolated(SETUP + r'''
EXT._RAMP_GENERATION = 1
token = B.capture_output_token()
remote_token = B.capture_output_token(client_id='remote', slot_id='same-slot')
B.manual_session.update(active=True, a={'configured_duration': -1}, b=None)
B.on_message(B.dg_ws, json.dumps({'type': 'client_disconnected', 'clientId': 'old'}))
assert not B.manual_continuous_status()['active']
assert not MP._default_route_snapshot()['selected']
B.on_message(B.dg_ws, json.dumps({'type': 'client_attached', 'clientId': 'old'}))
MP._update_multi_device_data('old', {
    'ev': 'devices.snapshot', 'devices': [{
        'slotId': 'same-slot', 'name': 'old',
        'slotState': {'hasDevice': True},
        'props': {'connectState': 'connected'},
    }],
})
assert MP.set_default_device('old', 'same-slot')[0]
assert not B.is_output_token_current(token)
assert B.is_output_token_current(remote_token)
wire.clear()
EXT.time = Clock()
EXT._ramp_worker(B.send_rpc, 1, 'same-slot', 0, 80, 5000, 1000, 4, token)
assert not operations()
assert B.is_output_token_current(B.capture_output_token())
''')

    def test_local_death_does_not_cancel_another_players_ramp(self):
        run_isolated(SETUP + r'''
B.rules['hp']['ramp_enabled'] = True
MP._handle_multiplayer_packet(packet(hp=80))
worker = queued.pop()
remote_token = worker.args[-1]
local_token = B.capture_output_token()
previous = dict(B.latest_peak)
current = dict(previous, dead=True, hp=0)
B.latest_peak = current
with V._LOCK:
    V.graphs = []
B.handle_game_rules(current, previous)
assert clears() and all(frame['clientId'] == 'old' for frame in clears()), wire
assert not B.is_output_token_current(local_token)
assert B.is_output_token_current(remote_token)
MP.time = Clock()
worker.run()
assert any(frame['clientId'] == 'remote' and frame['data']['data'].get('t') == 4
           and frame['data']['data']['v'] > 0 for frame in operations()), wire
''')

    def test_unbind_or_reenable_does_not_revive_queued_remote_work(self):
        run_isolated(SETUP + r'''
B.rules['hp']['ramp_enabled'] = True
MP._handle_multiplayer_packet(packet(hp=80))
worker = queued.pop()
assert MP.unbind_player_device('p')[0]
assert MP.bind_player_device('p', 'remote', 'same-slot')[0]
assert MP.set_player_binding_enabled('p', True)[0]
wire.clear()
MP.time = Clock()
worker.run()
assert not operations()
MP._handle_multiplayer_packet(packet(hp=70))
worker = queued.pop()
MP.set_remote_output_enabled(False)
MP.set_remote_output_enabled(True)
wire.clear()
worker.run()
assert not operations()
''')


if __name__ == '__main__':
    unittest.main()
