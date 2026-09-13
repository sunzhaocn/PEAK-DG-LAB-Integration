"""Runtime/UI hardening for the Coyote visual rule graph.

The base graph editor lives in visual_rules.py. This layer keeps that file small
and reviewable while enforcing takeover semantics and exposing parameters that
belong to the existing rule detectors.

Key guarantees:

1. Every enabled graph is evaluated for the same telemetry packet.
2. Death and passed-out graphs keep their dedicated graph topology.
3. Visual event detection is independent of built-in enable flags and output.
4. Detector parameters (speed threshold, item filter, recovery threshold, area
   definitions, etc.) travel with visual trigger nodes instead of requiring the
   old rule to stay enabled. Each trigger node owns its detection state.
5. The existing HP intensity-ramp feature is available from the visual intensity
   node and reuses extended_features.py's already-tested ramp worker.
6. Repeat/continuous graphs use Coyote's effective cooldown calculation.
"""
from __future__ import annotations

import time

import backend as B
import extended_features as EXT
import visual_rules as V

_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_VALIDATE = None
_SPECIAL_TYPES = {"death", "passed"}
_SPECIAL_ALLOWED = {
    "death",
    "passed",
    "output",
    "intensity",
    "duration",
    "waveform",
    "threshold",
    "spike",
    "random_waveform",
    "edge",
    "cooldown",
    "comment",
}

# Fields shared with the built-in detector UI. Runtime visual detection reads
# only node parameters; output-only fields deliberately do not belong here.
_DETECTOR_FIELDS = {
    "staminaUse": ("trigger_delta",),
    "speedBelow": ("speed_threshold",),
    "speedAbove": ("speed_threshold",),
    "heldItem": ("item_filter",),
    "backpackItem": ("item_filter",),
    "heldState": ("item_filter",),
    "backpackState": ("item_filter",),
    "consumedItem": ("item_filter",),
    "hpRecover": ("trigger_delta", "trigger_mode"),
    "staminaRecover": ("trigger_delta", "trigger_mode"),
    "statusRecover": ("trigger_delta",),
    "areaEnter": ("area_zones",),
    "areaDwell": ("area_zones", "area_dwell_seconds", "trigger_mode"),
}


def _snapshot_graphs():
    with V._LOCK:
        return list(V.graphs), set(V.valid_graph_ids)


def _has_enabled_special(node_type):
    snapshot, good = _snapshot_graphs()
    return any(
        graph.get("enabled")
        and graph.get("id") in good
        and any(node.get("type") == node_type for node in graph.get("nodes", []))
        for graph in snapshot
    )


def _normalise_detector_value(key, field, value):
    if field == "trigger_delta":
        try:
            return max(0.1, min(100.0, float(value)))
        except Exception:
            return 1.0

    if field == "speed_threshold":
        try:
            return max(0.0, min(1000.0, float(value)))
        except Exception:
            return 1.0 if key == "speedBelow" else 5.0

    if field == "item_filter":
        return str(value or "")[:500]

    if field == "trigger_mode":
        return "repeat" if str(value or "single").lower() in {"repeat", "while", "continuous"} else "single"

    if field == "area_zones":
        normalizer = getattr(B, "normalize_area_zones", None)
        if callable(normalizer):
            try:
                return normalizer(value)
            except Exception:
                return []
        return value if isinstance(value, list) else []

    if field == "area_dwell_seconds":
        try:
            return max(0.5, min(86400.0, float(value)))
        except Exception:
            return 30.0

    return value


def _trigger_defaults(key):
    """Snapshot only detector-owned fields for a trigger node."""
    fields = _DETECTOR_FIELDS.get(key, ())
    if not fields:
        return {}
    with B.rule_lock:
        cfg = B.rules.get(key, {})
        if not isinstance(cfg, dict):
            return {}
        return {
            field: V._copy(cfg.get(field))
            for field in fields
            if field in cfg
        }


def _detect_visual_trigger(graph, node, current, previous):
    """Detect an event using this node's settings and private runtime only.

    A visual event is not a successful built-in device send. In particular,
    single recovery/dwell events are consumed when detected, even if built-ins
    are disabled or a downstream visual condition prevents output.
    """
    # Discard deltas across rebuilt characters/scenes, just as the built-in
    # event detectors do. This is not an incapacitation lock: events during a
    # stable dead/passed-out state remain available to custom graphs.
    transition = EXT._packet_transition_reason(current, previous)
    guarded = (
        EXT.extension_settings.get("respawn_guard_enabled", True)
        and time.monotonic() < EXT._RESPAWN_GUARD_UNTIL
    )
    if transition or guarded:
        with V._RUNTIME_LOCK:
            V._rt(graph["id"], node["id"]).clear()
        return False
    params = node.get("params", {})
    key = str(params.get("rule_key") or "").strip()
    defaults = {
        "trigger_delta": 1.0,
        "speed_threshold": 5.0 if key == "speedAbove" else 1.0,
        "item_filter": "",
        "trigger_mode": "single",
        "area_zones": [],
        "area_dwell_seconds": 30.0,
    }
    cfg = {
        field: _normalise_detector_value(key, field, params.get(field, defaults[field]))
        for field in _DETECTOR_FIELDS.get(key, ())
    }
    with V._RUNTIME_LOCK:
        state = V._rt(graph["id"], node["id"])
        identity = {"rule_key": key, **cfg}
        if state.get("detector_config") != identity:
            state.clear()
            state["detector_config"] = V._copy(identity)
        return _detect_visual_event(key, cfg, state, current, previous)


def _detect_visual_event(key, cfg, state, current, previous):
    """Pure telemetry predicates; shared helpers preserve built-in field units."""
    if key == "hp":
        return round(V._num(current.get("hp", 100)), 1) < round(V._num(previous.get("hp", 100)), 1)
    if key == "staminaUse":
        return B.stamina_percent(previous) - B.stamina_percent(current) >= cfg["trigger_delta"]
    if key in {"speedBelow", "speedAbove"}:
        old, new = B.packet_speed(previous), B.packet_speed(current)
        threshold = cfg["speed_threshold"]
        return old >= threshold > new if key == "speedBelow" else old <= threshold < new
    if key == "jump":
        return (
            V._num(current.get("jumpSeq")) > V._num(previous.get("jumpSeq"))
            or (
                "jumpSeq" not in current
                and bool(previous.get("grounded", False))
                and not bool(current.get("grounded", False))
                and B.velocity_y(current) > 0.35
            )
        )
    if key in {"climbStart", "crouchStart"}:
        field = "climbing" if key == "climbStart" else "crouching"
        return bool(current.get(field, False)) and not bool(previous.get(field, False))
    if key == "heldItem":
        old = str((previous.get("heldItem") or {}).get("name", "") or "").strip()
        new = str((current.get("heldItem") or {}).get("name", "") or "").strip()
        return new != old and B.item_rule_matches(cfg, new)
    if key == "backpackItem":
        old = (previous.get("inventory") or {}).get("backpackItems", [])
        new = (current.get("inventory") or {}).get("backpackItems", [])
        return any(B.item_rule_matches(cfg, item) for item in B.list_added_items(old, new))
    if key in {"heldState", "backpackState"}:
        matcher = B.current_held_match if key == "heldState" else B.current_backpack_matches
        return matcher(current, cfg)[0] and not matcher(previous, cfg)[0]
    if key == "consumedItem":
        new_id = EXT._event_id(current, "lastConsumedItem")
        item = str((current.get("lastConsumedItem") or {}).get("item", "") or "").strip()
        return bool(new_id and new_id != EXT._event_id(previous, "lastConsumedItem")) and (
            not item or B.item_rule_matches(cfg, item)
        )
    if key in {"hpRecover", "staminaRecover"}:
        getter = (lambda packet: V._num(packet.get("hp", 100))) if key == "hpRecover" else B.stamina_percent
        old, new = getter(previous), getter(current)
        if new <= old + 1e-4:
            state.update(start=None, fired=False)
            return False
        if state.get("start") is None:
            state.update(start=old, fired=False)
        if new - state["start"] + 1e-6 < cfg["trigger_delta"]:
            return False
        if cfg["trigger_mode"] == "single" and state.get("fired", False):
            return False
        state["fired"] = True
        return True
    if key == "statusRecover":
        names = current.get("statusNames") or [name for name, _ in B.STATUS_ORDER]
        for index, name in enumerate(names):
            old = B.status_percent_for_rule(previous, name, index)
            new = B.status_percent_for_rule(current, name, index)
            if old is not None and new is not None and old - new + 1e-6 >= cfg["trigger_delta"]:
                return True
        return False
    if key in {"areaEnter", "areaDwell"}:
        return _detect_visual_area(key, cfg, state, current)
    for rule_key, _, index, _ in B.RULE_META:
        if key == rule_key and index is not None:
            old = B.status_percent_for_rule(previous, key, index)
            new = B.status_percent_for_rule(current, key, index)
            return old is not None and new is not None and new > old
    return False


def _detect_visual_area(key, cfg, state, current):
    position = EXT._packet_position(current)
    if position is None:
        return False
    scene = str(current.get("scene", "") or "").lower()
    now = time.monotonic()
    zones = state.setdefault("zones", {})
    detected = False
    for index, zone in enumerate(cfg["area_zones"]):
        scene_filter = zone["scene"].lower()
        inside = (not scene_filter or scene_filter in scene) and sum(
            (position[axis] - zone[name]) ** 2 for axis, name in enumerate(("x", "y", "z"))
        ) <= zone["radius"] ** 2
        zone_state = zones.setdefault(index, {"inside": False, "entered_at": None, "fired": False})
        was_inside = zone_state["inside"]
        if not inside:
            zone_state.update(inside=False, entered_at=None, fired=False)
            continue
        if not was_inside:
            zone_state.update(inside=True, entered_at=now, fired=False)
        if key == "areaEnter":
            detected = not was_inside or detected
        elif now - zone_state["entered_at"] + 1e-6 >= cfg["area_dwell_seconds"]:
            if cfg["trigger_mode"] == "repeat" or not zone_state["fired"]:
                zone_state["fired"] = True
                detected = True
    return detected


def _strict_validate_graph(graph):
    ok, message = _ORIGINAL_VALIDATE(graph)
    if not ok:
        return ok, message

    normalized = V._normalize(graph)
    special = [node for node in normalized.get("nodes", []) if node.get("type") in _SPECIAL_TYPES]
    if not special:
        return True, message

    special_type = special[0].get("type")
    for node in normalized.get("nodes", []):
        node_type = node.get("type")
        if node_type not in _SPECIAL_ALLOWED:
            return (
                False,
                "死亡/昏迷专用图必须独立：只能包含专用触发器、边沿/冷却、输出参数、电击输出和备注。",
            )
        if node_type in _SPECIAL_TYPES and node_type != special_type:
            return False, "死亡图和昏迷图必须分别建立，不能放在同一张规则图中。"

    if any(node.get("type") == "disable_builtin" for node in normalized.get("nodes", [])):
        return False, "“禁用软件内置规则”必须放在普通规则图中，不能放进死亡/昏迷专用图。"

    return True, "校验通过（死亡/昏迷专用规则已隔离）"


def _visual_ramp_from_output(graph, output_node, current, previous, cache):
    result = {"enabled": False, "duration_ms": 1500, "steps": 10}
    for edge in V._incoming(graph, output_node.get("id"), "intensity"):
        value = V._eval(graph, edge.get("from"), current, previous, cache, set())
        if not isinstance(value, dict) or value.get("kind") != "intensity":
            continue
        result["enabled"] = bool(value.get("ramp_enabled", False))
        try:
            result["duration_ms"] = max(100, min(60000, int(float(value.get("ramp_duration_ms", 1500)))))
        except Exception:
            result["duration_ms"] = 1500
        try:
            result["steps"] = max(2, min(100, int(float(value.get("ramp_steps", 10)))))
        except Exception:
            result["steps"] = 10
    return result


def install():
    global _INSTALLED, _ORIGINAL_VALIDATE
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_VALIDATE = V.validate_graph
    V.validate_graph = _strict_validate_graph
    V.detect_trigger = _detect_visual_trigger
    B.validate_visual_graph = _strict_validate_graph

    def evaluate_all(current, previous, privileged=False):
        with V._LOCK:
            snapshot = list(V.graphs)
        sent = False
        for graph in snapshot:
            sent = V.evaluate_graph(graph, current, previous, privileged) or sent
        return sent

    V.evaluate_all = evaluate_all

    original_special = V._send_special_builtin

    def send_special_builtin(key, name, detail):
        node_type = "death" if key == "dead" else "passed" if key == "passedOut" else ""
        if node_type and _has_enabled_special(node_type):
            return False
        return original_special(key, name, detail)

    V._send_special_builtin = send_special_builtin

    original_config = V._config

    def config(graph, output_node, current, previous, cache):
        cfg, pool = original_config(graph, output_node, current, previous, cache)
        ramp = _visual_ramp_from_output(graph, output_node, current, previous, cache)
        cfg["_visual_ramp_enabled"] = bool(ramp["enabled"])
        cfg["_visual_ramp_duration_ms"] = int(ramp["duration_ms"])
        cfg["_visual_ramp_steps"] = int(ramp["steps"])
        return cfg, pool

    V._config = config

    original_send_graph = V._send_graph

    def send_graph(graph, output_node, cfg, pool, value, delta, privileged):
        global _EXT_RAMP_GENERATION_PLACEHOLDER
        cfg = V._copy(cfg)
        params = output_node.get("params", {}) if isinstance(output_node, dict) else {}
        mode = str(params.get("mode", "edge") or "edge").lower()
        repeated = (
            mode in {"while", "repeat"}
            or B.is_continuous_duration(cfg.get("play_time_a", 1000))
            or B.is_continuous_duration(cfg.get("play_time_b", 1000))
        )
        if repeated:
            cfg["cooldown"] = B.continuous_effective_cooldown(cfg)

        # Reuse extended_features.py's existing absolute-intensity ramp hook.
        # Privileged death/passed-out output stays immediate so the ramp worker's
        # normal incapacitation guard cannot accidentally cancel the special rule.
        ramp = bool(cfg.get("_visual_ramp_enabled", False)) and not privileged
        if ramp:
            with EXT._RAMP_LOCK:
                EXT._RAMP_GENERATION += 1
                generation = EXT._RAMP_GENERATION
            EXT._RAMP_CONTEXT.value = {
                "generation": generation,
                "ramp_duration_ms": int(cfg.get("_visual_ramp_duration_ms", 1500)),
                "ramp_steps": int(cfg.get("_visual_ramp_steps", 10)),
            }
            B.add_log(
                "图形规则",
                "强度渐升",
                (
                    f"{graph.get('name', '规则图')} | "
                    f"渐升={int(cfg.get('_visual_ramp_duration_ms', 1500))}ms / "
                    f"步数={int(cfg.get('_visual_ramp_steps', 10))}"
                ),
            )

        try:
            return original_send_graph(graph, output_node, cfg, pool, value, delta, privileged)
        finally:
            if ramp:
                EXT._RAMP_CONTEXT.value = None

    V._send_graph = send_graph

    B.COYOTE_VISUAL_RULES_HARDENING = 3


def _enhance_editor(editor, UI):
    """Expose detector/ramp properties without replacing the base graph canvas."""
    cls = type(editor)
    if getattr(cls, "_coyote_detector_fields_installed", False):
        return
    cls._coyote_detector_fields_installed = True

    original_add = cls.add
    original_show_props = cls.show_props

    def enrich_node(node_data):
        if not isinstance(node_data, dict):
            return False
        params = node_data.setdefault("params", {})
        if node_data.get("type") == "trigger":
            key = str(params.get("rule_key") or "").strip()
            changed = False
            for field, value in _trigger_defaults(key).items():
                if field not in params:
                    params[field] = V._copy(value)
                    changed = True
            return changed
        if node_data.get("type") == "intensity":
            additions = {
                "ramp_enabled": False,
                "ramp_duration_ms": 1500,
                "ramp_steps": 10,
            }
            changed = False
            for field, value in additions.items():
                if field not in params:
                    params[field] = value
                    changed = True
            return changed
        return False

    def add(self, item, column=0):
        before = {node.get("id") for node in (self.current or {}).get("nodes", [])} if self.current else set()
        result = original_add(self, item, column)
        if self.current:
            changed = False
            for node in self.current.get("nodes", []):
                if node.get("id") not in before:
                    changed = enrich_node(node) or changed
            if changed:
                self.rebuild()
        return result

    def show_props(self, node):
        try:
            enrich_node(node.data)
        except Exception:
            pass
        return original_show_props(self, node)

    cls.add = add
    cls.show_props = show_props

    try:
        editor.status.setText(
            "节点参数已覆盖现有规则检测配置；强度节点含受伤渐升参数。"
            "先点输出端口，再点输入端口连线；Delete 删除；滚轮缩放。"
        )
    except Exception:
        pass


def install_ui(UI):
    """Install after V.install_ui(UI)."""
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return
    _UI_INSTALLED = True

    BaseWindow = UI.Window

    class HardenedVisualWindow(BaseWindow):
        def build_custom_code(self):
            super().build_custom_code()
            try:
                _enhance_editor(self.visual_rule_editor, UI)
            except Exception as exc:
                B.add_log("错误", "图形规则参数模块安装失败", repr(exc))

    UI.Window = HardenedVisualWindow
