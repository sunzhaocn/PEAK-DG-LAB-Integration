"""Runtime hardening for the Coyote visual rule graph.

This layer keeps visual_rules.py isolated while enforcing the execution rules that
matter most for a node-based takeover:

1. Every enabled graph is evaluated on the same telemetry packet (no any()
   short-circuit).
2. An enabled visual death/passed-out graph owns that special edge and prevents
   the legacy special fallback from double-firing.
3. Repeat/continuous visual output reuses Coyote's effective cooldown so finite
   DG-LAB segments are not stacked unnecessarily.
4. When an enabled graph contains ``disable_builtin``, visual trigger nodes may
   still use the existing built-in event detectors even if those built-in rules
   are switched off in the normal rule UI. Detector enablement is temporary and
   the visual output gate still suppresses every ordinary built-in shock.
5. Death/passed-out graphs are strictly isolated from ordinary trigger/condition
   graphs and from the global ``disable_builtin`` module.
"""
from __future__ import annotations

from contextlib import contextmanager

import backend as B
import visual_rules as V

_INSTALLED = False
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


def _enabled_trigger_keys():
    """Return ordinary built-in event keys currently consumed by visual graphs."""
    snapshot, good = _snapshot_graphs()
    result = set()
    for graph in snapshot:
        if not graph.get("enabled") or graph.get("id") not in good:
            continue
        for node in graph.get("nodes", []):
            if node.get("type") != "trigger":
                continue
            params = node.get("params") if isinstance(node.get("params"), dict) else {}
            key = str(params.get("rule_key") or "").strip()
            if key and key not in V._SPECIAL_KEYS and key in getattr(B, "rules", {}):
                result.add(key)
    return result


@contextmanager
def _temporarily_enable_visual_event_detectors():
    """Enable subscribed detector configs only while built-in output is suppressed.

    Several current extension detectors (consume/recovery/area) deliberately skip
    all work when their normal B.rules entry is disabled. A visual graph using
    ``disable_builtin`` must still be able to consume those events. We therefore
    turn on only the subscribed detector entries for the duration of one telemetry
    dispatch and restore the user's real rule settings immediately afterwards.

    V.install_backend() already wraps B.send_rule_output so built-in output is
    rejected before device I/O whenever ``disable_builtin`` is active. This makes
    the temporary enablement detector-only; it cannot resurrect ordinary shocks.
    """
    if not V.builtins_disabled():
        yield
        return

    keys = _enabled_trigger_keys()
    if not keys:
        yield
        return

    saved = {}
    with B.rule_lock:
        for key in keys:
            cfg = B.rules.get(key)
            if not isinstance(cfg, dict):
                continue
            saved[key] = bool(cfg.get("enabled", False))
            cfg["enabled"] = True

    try:
        yield
    finally:
        with B.rule_lock:
            for key, enabled in saved.items():
                cfg = B.rules.get(key)
                if isinstance(cfg, dict):
                    cfg["enabled"] = enabled


def _strict_validate_graph(graph):
    ok, message = _ORIGINAL_VALIDATE(graph)
    if not ok:
        return ok, message

    normalized = V._normalize(graph)
    special = [node for node in normalized.get("nodes", []) if node.get("type") in _SPECIAL_TYPES]
    if not special:
        return True, message

    # Exactly one special event source per special graph. The base validator
    # already prevents death + passed in one graph; this makes the isolation rule
    # explicit and rejects global/ordinary logic living beside it.
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
        # Do not use any(...): it short-circuits after the first True and would
        # prevent later independent graphs from running on the same packet.
        for graph in snapshot:
            sent = V.evaluate_graph(graph, current, previous, privileged) or sent
        return sent

    V.evaluate_all = evaluate_all

    original_special = V._send_special_builtin

    def send_special_builtin(key, name, detail):
        node_type = "death" if key == "dead" else "passed" if key == "passedOut" else ""
        # A valid enabled visual special graph has precedence. Legacy dead/passed
        # settings remain a fallback only when no visual graph owns that edge.
        if node_type and _has_enabled_special(node_type):
            return False
        return original_special(key, name, detail)

    V._send_special_builtin = send_special_builtin

    original_send_graph = V._send_graph

    def send_graph(graph, output_node, cfg, pool, value, delta, privileged):
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
        return original_send_graph(graph, output_node, cfg, pool, value, delta, privileged)

    V._send_graph = send_graph

    # V.install_backend() has already replaced B.handle_game_rules. Wrap that
    # final visual dispatcher from the outside so subscribed built-in detectors
    # can run even when the takeover module suppresses their actual output.
    visual_handle_game_rules = B.handle_game_rules

    def handle_game_rules(current, previous):
        with _temporarily_enable_visual_event_detectors():
            return visual_handle_game_rules(current, previous)

    B.handle_game_rules = handle_game_rules

    B.COYOTE_VISUAL_RULES_HARDENING = 2
