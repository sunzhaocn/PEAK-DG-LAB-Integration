"""Offline regressions for stop barriers and manual playback replacement."""
import unittest

from review_support import run_isolated


FAKE_DEVICE = r'''
import sys
import time

packets = []
class FakeWebSocket:
    def send(self, text):
        packets.append(json.loads(text))

B.dg_ws = FakeWebSocket()
B.dg.update(app_id="app-one", slot_id="slot-one", controller_id="controller")
B.master_output_enabled = True
B.latest_peak = {"dead": False, "passedOut": False}
EXT.extension_settings.update(global_guard_enabled=False, respawn_guard_enabled=False)
B.rules["hp"].update(enabled=True, cooldown=0, ramp_enabled=False)
B.add_log = lambda *args, **kwargs: None

def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for the expected worker state")

def operations():
    return [p["data"] for p in packets if p["data"].get("m") == "device.op"]

def finish_manual_workers(workers):
    B.master_output_enabled = False
    B.clear_device_output("test cleanup")
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive(), "Manual worker did not stop"
'''


class OutputControlTests(unittest.TestCase):
    def test_stop_cancels_every_pending_stage_even_after_reenable(self):
        run_isolated(FAKE_DEVICE + r'''
for blocked_stage in range(1, 5):
    packets.clear()
    B.last_trigger_time.clear()
    paused, resume = threading.Event(), threading.Event()
    reached = [0]
    result, errors = [], []

    def trace(frame, event, arg):
        if event == "call" and frame.f_code is B.send_payload.__code__:
            reached[0] += 1
            if reached[0] == blocked_stage:
                paused.set()
                assert resume.wait(3), "Scheduling barrier was not released"
        return trace

    def fire_rule():
        sys.settrace(trace)
        try:
            result.append(B.send_rule_output("hp", "test", "test", current_value_pct=80))
        except BaseException as exc:
            errors.append(exc)
        finally:
            sys.settrace(None)

    worker = threading.Thread(target=fire_rule)
    worker.start()
    assert paused.wait(3), "Rule did not reach the selected RPC stage"
    B.master_output_enabled = False
    assert B.clear_device_output("emergency stop")[0]
    stop_index = len(packets)
    assert packets[-1]["data"]["m"] == "device.op.clear"
    # A switch check alone cannot pass this regression: stale work must remain
    # cancelled even after a new, separately authorized output is allowed.
    B.master_output_enabled = True
    resume.set()
    worker.join(3)
    assert not worker.is_alive()
    assert not errors, errors
    assert result == [False], result
    assert packets[stop_index:] == [], packets[stop_index:]

# A fresh event still works after the stop barrier.
B.last_trigger_time.clear()
packets.clear()
assert B.send_rule_output("hp", "fresh", "fresh", current_value_pct=80)
assert len(operations()) == 4
assert all(p["clientId"] == "app-one" for p in packets)
''')

    def test_complete_destination_and_socket_are_pinned(self):
        run_isolated(FAKE_DEVICE + r'''
token = B.capture_output_token()
command = {"s": "slot-one", "c": 0, "t": 4, "v": 10, "d": 1000, "im": True}
with B.output_context(token):
    assert B.send_rpc("device.op", command)[0]
    initial_count = len(packets)
    # Two phone clients may expose the same slot ID. Do not redirect an old
    # output merely because its slot string matches the new default device.
    with B.ws_send_lock:
        B.dg["app_id"] = "app-two"
    assert not B.send_rpc("device.op", command)[0]
    assert len(packets) == initial_count
    with B.ws_send_lock:
        B.dg["app_id"] = "app-one"
        B.dg_ws = FakeWebSocket()
    assert not B.send_rpc("device.op", command)[0]
    assert len(packets) == initial_count

fresh = B.capture_output_token()
with B.output_context(fresh):
    assert not B.send_rpc("device.op", dict(command, s="another-slot"))[0]
    assert B.send_rpc("device.op", command)[0]

# Clearing is always permitted after the master switch is disabled.
B.master_output_enabled = False
with B.output_context(token):
    assert B.clear_device_output("disabled master")[0]
assert packets[-1]["data"]["m"] == "device.op.clear"
''')

    def test_local_route_cancellation_does_not_cancel_remote_output(self):
        run_isolated(FAKE_DEVICE + r'''
local = B.capture_output_token()
remote = B.capture_output_token(client_id="remote-app", slot_id="remote-slot")
B.cancel_pending_output(local_only=True)
assert not B.is_output_token_current(local)
assert B.is_output_token_current(remote)
assert B.clear_device_output("local character incapacitated", local_only=True)[0]
assert B.is_output_token_current(remote)
with B.output_context(remote):
    command = {"t": "req", "m": "device.op", "data": {
        "s": "remote-slot", "c": 0, "t": 4, "v": 10, "d": 1000, "im": True}}
    assert B.send_payload(command, client_id="remote-app")[0]
assert packets[-1]["clientId"] == "remote-app"
B.cancel_pending_output()
assert not B.is_output_token_current(remote)
''')

    def test_superseded_worker_cannot_send_or_clear_replacement(self):
        run_isolated(FAKE_DEVICE + r'''
paused, resume, old_done = threading.Event(), threading.Event(), threading.Event()
workers = []
original_worker = B._manual_continuous_worker
def controlled_worker(generation, token):
    workers.append(threading.current_thread())
    first = len(workers) == 1
    def trace(frame, event, arg):
        if first and event == "call" and frame.f_code is B.send_payload.__code__ and not paused.is_set():
            paused.set()
            assert resume.wait(3), "Scheduling barrier was not released"
        return trace
    sys.settrace(trace)
    try:
        return original_worker(generation, token)
    finally:
        sys.settrace(None)
        if first:
            old_done.set()
B._manual_continuous_worker = controlled_worker

assert B.send_manual_channel(0, 5, -1, "脉冲")[0]
assert paused.wait(3)
assert B.send_manual_channel(0, 10, -1, "脉冲")[0]
new_generation = B.manual_session["generation"]
wait_for(lambda: any(p["data"].get("t") == 0 for p in operations()))
resume.set()
assert old_done.wait(3)
assert B.manual_continuous_status() == {"active": True, "a": True, "b": False}
assert B.manual_session["generation"] == new_generation
assert not any(p["data"]["m"] == "device.op.clear" for p in packets)
assert all(p["data"]["v"] == 10 for p in operations() if p["data"]["t"] == 4)
# Verify that the new session also survives the following renewal.
wait_for(lambda: len(operations()) >= 4)
finish_manual_workers(workers)
''')

    def test_continuous_to_finite_single_and_dual_cancel_old_renewal(self):
        run_isolated(FAKE_DEVICE + r'''
for dual in (False, True):
    B.master_output_enabled = True
    packets.clear()
    paused, resume, old_done = threading.Event(), threading.Event(), threading.Event()
    workers = []
    original_worker = B._manual_continuous_worker
    def controlled_worker(generation, token):
        workers.append(threading.current_thread())
        count = [0]
        def trace(frame, event, arg):
            if event == "call" and frame.f_code is B.send_payload.__code__:
                count[0] += 1
                if count[0] == 3:  # First RPC of the next continuous segment.
                    paused.set()
                    assert resume.wait(3), "Scheduling barrier was not released"
            return trace
        sys.settrace(trace)
        try:
            return original_worker(generation, token)
        finally:
            sys.settrace(None)
            old_done.set()
    B._manual_continuous_worker = controlled_worker
    assert B.send_manual_channel(0, 5, -1, "脉冲")[0]
    assert paused.wait(3)
    replacement_index = len(packets)
    if dual:
        assert B.send_manual_dual(10, 11, 5000, 3000, "脉冲", "脉冲")["ok"]
    else:
        assert B.send_manual_channel(0, 10, 5000, "脉冲")[0]
    resume.set()
    assert old_done.wait(3)
    assert not B.manual_continuous_status()["active"]
    replacement = packets[replacement_index:]
    assert len(replacement) == (4 if dual else 2), replacement
    assert all(p["data"]["m"] == "device.op" for p in replacement)
    assert all(p["data"]["data"]["v"] in (10, 11)
               for p in replacement if p["data"]["data"]["t"] == 4)
    finish_manual_workers(workers)
    B._manual_continuous_worker = original_worker
''')

    def test_cancelled_manual_cleanup_cannot_clear_fresh_automatic_output(self):
        run_isolated(FAKE_DEVICE + r'''
paused, resume, done = threading.Event(), threading.Event(), threading.Event()
workers = []
original_worker = B._manual_continuous_worker
def controlled_worker(*args):
    workers.append(threading.current_thread())
    def trace(frame, event, arg):
        if event == "call" and frame.f_code is B.send_payload.__code__ and not paused.is_set():
            paused.set()
            assert resume.wait(3), "Scheduling barrier was not released"
        return trace
    sys.settrace(trace)
    try:
        return original_worker(*args)
    finally:
        sys.settrace(None)
        done.set()
B._manual_continuous_worker = controlled_worker
assert B.send_manual_channel(0, 5, -1, "脉冲")[0]
assert paused.wait(3)
B.cancel_pending_output(local_only=True)
assert B.send_rule_output("hp", "fresh", "fresh", current_value_pct=80)
fresh_packets = list(packets)
assert len(fresh_packets) == 4
resume.set()
assert done.wait(3)
assert packets == fresh_packets, "Cancelled worker touched the fresh output"
assert not B.manual_continuous_status()["active"]
finish_manual_workers(workers)
''')

    def test_mixed_dual_replacement_keeps_only_requested_continuous_channel(self):
        run_isolated(FAKE_DEVICE + r'''
workers = []
original_worker = B._manual_continuous_worker
def tracked_worker(*args):
    workers.append(threading.current_thread())
    return original_worker(*args)
B._manual_continuous_worker = tracked_worker
assert B.send_manual_channel(0, 5, -1, "脉冲")[0]
wait_for(lambda: len(operations()) >= 2)
replacement_index = len(packets)
assert B.send_manual_dual(10, 12, 5000, -1, "脉冲", "脉冲")["ok"]
wait_for(lambda: sum(p["data"].get("c") == 1 for p in operations()) >= 4)
assert B.manual_continuous_status() == {"active": True, "a": False, "b": True}
replacement = packets[replacement_index:]
assert all(p["data"]["m"] == "device.op" for p in replacement)
a_levels = [p["data"]["data"]["v"] for p in replacement
            if p["data"]["data"].get("c") == 0 and p["data"]["data"].get("t") == 4]
assert a_levels == [10], a_levels
finish_manual_workers(workers)
''')

    def test_finite_channel_preserves_other_continuous_channel(self):
        run_isolated(FAKE_DEVICE + r'''
for finite_channel in (0, 1):
    B.master_output_enabled = True
    packets.clear()
    paused, resume, old_done = threading.Event(), threading.Event(), threading.Event()
    workers = []
    original_worker = B._manual_continuous_worker
    def controlled_worker(generation, token):
        workers.append(threading.current_thread())
        first = len(workers) == 1
        count = [0]
        def trace(frame, event, arg):
            if first and event == "call" and frame.f_code is B.send_payload.__code__:
                count[0] += 1
                if count[0] == 5:  # A+B have each sent one full segment.
                    paused.set()
                    assert resume.wait(3), "Scheduling barrier was not released"
            return trace
        sys.settrace(trace)
        try:
            return original_worker(generation, token)
        finally:
            sys.settrace(None)
            if first:
                old_done.set()
    B._manual_continuous_worker = controlled_worker
    assert B.send_manual_dual(5, 6, -1, -1, "脉冲", "脉冲")["ok"]
    assert paused.wait(3)
    replacement_index = len(packets)
    assert B.send_manual_channel(finite_channel, 10, 5000, "脉冲")[0]
    resume.set()
    assert old_done.wait(3)
    wait_for(lambda: len(packets) >= replacement_index + 6)
    assert B.manual_continuous_status() == {
        "active": True, "a": finite_channel == 1, "b": finite_channel == 0}
    replacement = packets[replacement_index:]
    assert all(p["data"]["m"] == "device.op" for p in replacement)
    finite_levels = [p["data"]["data"]["v"] for p in replacement
                     if p["data"]["data"]["c"] == finite_channel
                     and p["data"]["data"]["t"] == 4]
    assert finite_levels == [10], finite_levels
    continuing_levels = [p["data"]["data"]["v"] for p in replacement
                         if p["data"]["data"]["c"] != finite_channel
                         and p["data"]["data"]["t"] == 4]
    assert len(continuing_levels) >= 2
    assert all(level == (6 if finite_channel == 0 else 5) for level in continuing_levels)
    finish_manual_workers(workers)
    B._manual_continuous_worker = original_worker
''')


if __name__ == "__main__":
    unittest.main()
