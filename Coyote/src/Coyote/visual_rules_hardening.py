"""Runtime hardening for the visual rule graph.

Kept as a small layer so the first graph implementation can remain isolated and
reviewable. The functions here fix three execution-boundary cases:
1. evaluate every enabled graph on the same telemetry packet (no any() short-circuit),
2. prefer an enabled visual death/passed-out graph over the legacy special rule
   fallback so the same edge cannot double-fire,
3. make repeated/continuous visual output reuse Coyote's existing effective
   cooldown calculation so finite DG-LAB segments do not overlap unnecessarily.
"""
from __future__ import annotations

import backend as B
import visual_rules as V

_INSTALLED = False


def _has_enabled_special(node_type):
    with V._LOCK:
        snapshot = list(V.graphs)
        good = set(V.valid_graph_ids)
    return any(
        graph.get("enabled")
        and graph.get("id") in good
        and any(node.get("type") == node_type for node in graph.get("nodes", []))
        for graph in snapshot
    )


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

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
        # A visual special graph has precedence. Legacy dead/passedOut settings
        # remain a fallback only when no enabled valid visual graph owns the edge.
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
    B.COYOTE_VISUAL_RULES_HARDENING = 1
