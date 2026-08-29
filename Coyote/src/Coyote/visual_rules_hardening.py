"""Runtime/UI hardening for the Coyote visual rule graph.

The base graph editor lives in visual_rules.py. This layer keeps that file small
and reviewable while enforcing takeover semantics and exposing parameters that
belong to the existing rule detectors.

Key guarantees:

1. Every enabled graph is evaluated for the same telemetry packet.
2. Death and passed-out graphs are isolated privileged domains.
3. ``disable_builtin`` suppresses ordinary built-in device output but does not
   starve visual event nodes of the existing event detectors.
4. Detector parameters (speed threshold, item filter, recovery threshold, area
   definitions, etc.) travel with visual trigger nodes instead of requiring the
   old rule to stay enabled.
5. The existing HP intensity-ramp feature is available from the visual intensity
   node and reuses extended_features.py's already-tested ramp worker.
6. Repeat/continuous graphs use Coyote's effective cooldown calculation.
"""
from __future__ import annotations

from contextlib import contextmanager

import backend as B
import extended_features as EXT
import visual_rules as V

_INSTALLED = False
_UI_INSTALLED = False
_ORIGINAL_VALIDATE = None
_MISSING = object()
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

# Fields that affect whether/when the current built-in event detector emits an
# event. Output-only fields deliberately do not belong here.
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


def _enabled_trigger_specs():
    """Return rule_key -> visual detector parameters for enabled valid graphs.

    The first enabled node for a key owns the detector configuration for that
    telemetry dispatch. Users can still build multiple branches from that event.
    """
    snapshot, good = _snapshot_graphs()
    result = {}
    for graph in snapshot:
        if not graph.get("enabled") or graph.get("id") not in good:
            continue
        for node in graph.get("nodes", []):
            if node.get("type") != "trigger":
                continue
            params = node.get("params") if isinstance(node.get("params"), dict) else {}
            key = str(params.get("rule_key") or "").strip()
            if not key or key in V._SPECIAL_KEYS or key not in getattr(B, "rules", {}):
                continue
            if key in result:
                continue
            merged = _trigger_defaults(key)
            for field in _DETECTOR_FIELDS.get(key, ()):
                if field in params:
                    merged[field] = _normalise_detector_value(key, field, params.get(field))
            result[key] = merged
    return result


@contextmanager
def _temporarily_enable_visual_event_detectors():
    """Run subscribed built-in detectors as detector-only when takeover is active.

    V.install_backend() records the event key before its ``disable_builtin`` gate
    and returns before device I/O. Temporarily enabling and parameterising these
    detector entries therefore cannot resurrect ordinary built-in stimulation.
    All mutated fields are restored in ``finally`` after the current telemetry
    packet finishes.
    """
    if not V.builtins_disabled():
        yield
        return

    specs = _enabled_trigger_specs()
    if not specs:
        yield
        return

    saved = {}
    with B.rule_lock:
        for key, params in specs.items():
            cfg = B.rules.get(key)
            if not isinstance(cfg, dict):
                continue
            snapshot = {"enabled": cfg.get("enabled", _MISSING)}
            cfg["enabled"] = True
            for field, value in params.items():
                snapshot[field] = cfg.get(field, _MISSING)
                cfg[field] = V._copy(value)
            saved[key] = snapshot

    try:
        yield
    finally:
        with B.rule_lock:
            for key, snapshot in saved.items():
                cfg = B.rules.get(key)
                if not isinstance(cfg, dict):
                    continue
                for field, value in snapshot.items():
                    if value is _MISSING:
                        cfg.pop(field, None)
                    else:
                        cfg[field] = value


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

    visual_handle_game_rules = B.handle_game_rules

    def handle_game_rules(current, previous):
        with _temporarily_enable_visual_event_detectors():
            return visual_handle_game_rules(current, previous)

    B.handle_game_rules = handle_game_rules

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
